#!/usr/bin/env python3
"""
audit-actions-cache.py

Audits GitHub Actions workflow files for cache configuration risks.
Recursively resolves composite actions and flags:
  - setup-* actions missing explicit cache: ''
  - actions/cache usage
  - Composite actions that internally cache without exposure

Usage:
  # Audit local workflow files
  python audit-actions-cache.py --workflow .github/workflows/publish.yml

  # Audit all workflows in a local repo
  python audit-actions-cache.py --repo-path /path/to/repo

  # Audit a remote repo (requires GITHUB_TOKEN)
  python audit-actions-cache.py --remote owner/repo
  python audit-actions-cache.py --remote owner/repo@v2.3.0

  # Audit multiple repos from a YAML file
  python audit-actions-cache.py --remotes-file repos.yml

  # Audit specific action refs directly (@* resolves to latest release)
  python audit-actions-cache.py --action owner/repo@sha --action owner/repo@*

  # Generate HTML report
  python audit-actions-cache.py --remotes-file repos.yml --html report.html

  # Set GitHub token via env
  export GITHUB_TOKEN=ghp_...

repos.yml format:
  - pypa/cibuildwheel@v2.23.0
  - psf/black
  - astral-sh/uv@8d2b08b68458a16aeb24b64e68a09ab1c8e82084
  - some-org/some-action@*   # resolves to latest release
"""

import os
import sys
import re
import json
import argparse
import textwrap
import datetime
from pathlib import Path
from typing import Optional
from urllib.request import urlopen, Request
from urllib.error import HTTPError

try:
    import yaml
except ImportError:
    print("Error: PyYAML is required. Run: pip install pyyaml")
    sys.exit(1)

# ── ANSI colours ─────────────────────────────────────────────────────────────
RED    = "\033[91m"
YELLOW = "\033[93m"
GREEN  = "\033[92m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
RESET  = "\033[0m"

# ── Actions known to support caching ─────────────────────────────────────────
CACHE_AWARE_ACTIONS: dict[str, dict] = {
    "actions/setup-node":            {"input": "cache",          "default_on_since": "v4"},
    "actions/setup-python":          {"input": "cache",          "default_on_since": "v4"},
    "actions/setup-java":            {"input": "cache",          "default_on_since": "v3"},
    "actions/setup-go":              {"input": "cache",          "default_on_since": "v4"},
    "actions/setup-dotnet":          {"input": "cache",          "default_on_since": "v4"},
    "actions/setup-ruby":            {"input": "bundler-cache",  "default_on_since": "v1"},
    "gradle/gradle-build-action":    {"input": "cache-disabled", "default_on_since": "v2"},
    "gradle/actions/setup-gradle":   {"input": "cache-disabled", "default_on_since": "v3"},
    "actions/cache":                 {"input": None,             "default_on_since": "v1"},
}

EXPLICIT_DISABLE: dict[str, list[tuple[str, str]]] = {
    "actions/setup-node":            [("cache", ""), ("cache", "false")],
    "actions/setup-python":          [("cache", ""), ("cache", "false")],
    "actions/setup-java":            [("cache", ""), ("cache", "false")],
    "actions/setup-go":              [("cache", "false")],
    "actions/setup-dotnet":          [("cache", "false")],
    "actions/setup-ruby":            [("bundler-cache", "false")],
    "gradle/gradle-build-action":    [("cache-disabled", "true")],
    "gradle/actions/setup-gradle":   [("cache-disabled", "true")],
}

# ── GitHub API helpers ────────────────────────────────────────────────────────

def github_token() -> Optional[str]:
    return os.environ.get("GITHUB_TOKEN")


def gh_api(url: str) -> dict | list | str:
    """Fetch a GitHub API or raw URL, returns parsed JSON or raw text."""
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "cache-auditor/1.0"}
    if github_token():
        headers["Authorization"] = f"Bearer {github_token()}"
    req = Request(url, headers=headers)
    try:
        with urlopen(req, timeout=15) as resp:
            raw = resp.read().decode()
    except HTTPError as exc:
        raise HTTPError(url, exc.code, f"{exc.reason} — {url}", exc.headers, exc.fp) from exc
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


# ── Ref classification & resolution ──────────────────────────────────────────

SHA_RE             = re.compile(r'^[0-9a-f]{40}$', re.IGNORECASE)
INVALID_REF_RE     = re.compile(r'[*?\[\]{}\\^~\s><=!]')
WILDCARD_LATEST_RE = re.compile(r'^\*$')


def is_sha_pin(ref: str) -> bool:
    return bool(SHA_RE.match(ref))


def is_wildcard_latest(ref: str) -> bool:
    """True for bare '*' — should resolve to the latest release."""
    return bool(WILDCARD_LATEST_RE.match(ref))


def is_invalid_ref(ref: str) -> bool:
    """True for non-'*' wildcards, ranges, whitespace — structurally unresolvable."""
    if is_wildcard_latest(ref):
        return False
    return bool(INVALID_REF_RE.search(ref))


_latest_release_cache: dict[str, Optional[str]] = {}


def resolve_latest_release(owner_repo: str) -> Optional[str]:
    """
    Return the tag_name of the latest GitHub release for owner_repo.
    Falls back to the first tag if no formal release exists.
    Results are cached for the lifetime of the process.
    """
    if owner_repo in _latest_release_cache:
        return _latest_release_cache[owner_repo]

    tag: Optional[str] = None
    url = f"https://api.github.com/repos/{owner_repo}/releases/latest"
    try:
        data = gh_api(url)
        tag = data.get("tag_name") if isinstance(data, dict) else None
    except HTTPError as e:
        if e.code != 404:
            print(f"{YELLOW}Warning: could not fetch latest release for {owner_repo}: {e}{RESET}",
                  file=sys.stderr)

    if tag is None:
        try:
            tags = gh_api(f"https://api.github.com/repos/{owner_repo}/tags")
            tag = tags[0]["name"] if isinstance(tags, list) and tags else None
        except Exception:
            pass

    _latest_release_cache[owner_repo] = tag
    return tag


def resolve_ref_to_sha(owner_repo: str, ref: str) -> tuple[str, bool, bool, Optional[str]]:
    """
    Resolve a ref to its commit SHA.

    Returns (sha, was_already_pinned, is_invalid, resolved_tag) where:
      was_already_pinned  – ref was already a 40-char SHA
      is_invalid          – ref is a non-'*' wildcard/glob (truly unresolvable)
      resolved_tag        – when ref was '*', the release tag it was expanded to

    For bare '*':  resolve to latest release tag, then to that tag's SHA.
    If the release lookup fails, treat as invalid.
    """
    if is_sha_pin(ref):
        return ref, True, False, None

    if is_invalid_ref(ref):
        return ref, False, True, None

    resolved_tag: Optional[str] = None

    if is_wildcard_latest(ref):
        tag = resolve_latest_release(owner_repo)
        if tag is None:
            return ref, False, True, None
        resolved_tag = tag
        ref = tag   # continue with the real tag name

    for api_ref in (f"tags/{ref}", f"heads/{ref}"):
        url = f"https://api.github.com/repos/{owner_repo}/git/ref/{api_ref}"
        try:
            data = gh_api(url)
            if isinstance(data, dict):
                obj = data.get("object", {})
                sha = obj.get("sha", "")
                if obj.get("type") == "tag":
                    tag_url = obj.get("url", "")
                    if tag_url:
                        tag_data = gh_api(tag_url)
                        if isinstance(tag_data, dict):
                            sha = tag_data.get("object", {}).get("sha", sha)
                if sha:
                    return sha, False, False, resolved_tag
        except HTTPError as e:
            if e.code != 404:
                print(f"{YELLOW}Warning: {e}{RESET}", file=sys.stderr)
            continue
        except Exception as e:
            print(f"{YELLOW}Warning resolving {url}: {e}{RESET}", file=sys.stderr)
            continue

    try:
        url = f"https://api.github.com/repos/{owner_repo}/commits/{ref}"
        data = gh_api(url)
        if isinstance(data, dict) and data.get("sha"):
            return data["sha"], False, False, resolved_tag
    except HTTPError as e:
        if e.code != 404:
            print(f"{YELLOW}Warning: {e}{RESET}", file=sys.stderr)
    except Exception as e:
        print(f"{YELLOW}Warning resolving {url}: {e}{RESET}", file=sys.stderr)

    return ref, False, False, resolved_tag


def fetch_action_yml(owner_repo: str, ref: str) -> Optional[dict]:
    for filename in ("action.yml", "action.yaml"):
        url = f"https://raw.githubusercontent.com/{owner_repo}/{ref}/{filename}"
        try:
            raw = gh_api(url)
            if isinstance(raw, str):
                return yaml.safe_load(raw)
        except HTTPError as e:
            if e.code == 404:
                continue
            raise
    return None


def parse_uses(uses: str) -> tuple[Optional[str], Optional[str]]:
    if uses.startswith("docker://") or uses.startswith("./"):
        return None, None
    parts = uses.split("@")
    if len(parts) != 2:
        return None, None
    repo_path, ref = parts
    segments = repo_path.split("/")
    if len(segments) < 2:
        return None, None
    return "/".join(segments[:2]), ref


def action_base_name(uses: str) -> str:
    owner_repo, _ = parse_uses(uses)
    return owner_repo or uses


# ── Finding ───────────────────────────────────────────────────────────────────

class Finding:
    def __init__(self, level: str, action: str, workflow: str,
                 job: Optional[str], message: str, recommendation: str,
                 resolved_sha:  Optional[str] = None,
                 resolved_tag:  Optional[str] = None,
                 cache_input:   Optional[str] = None,
                 cache_value:   Optional[str] = None,
                 repo:          Optional[str] = None):
        self.level          = level
        self.action         = action
        self.workflow       = workflow
        self.job            = job
        self.message        = message
        self.recommendation = recommendation
        self.resolved_sha   = resolved_sha
        self.resolved_tag   = resolved_tag
        self.cache_input    = cache_input
        self.cache_value    = cache_value
        self.repo           = repo   # owner/repo slug set by fetch_remote_workflows

    def colour(self) -> str:
        return {"HIGH": RED, "MEDIUM": YELLOW, "INFO": CYAN}.get(self.level, RESET)

    def __str__(self) -> str:
        c = self.colour()
        job_str = f" [{self.job}]" if self.job else ""
        return (
            f"  {c}{BOLD}[{self.level}]{RESET} {self.action}{DIM}{job_str}{RESET}\n"
            f"    {self.message}\n"
            f"    {GREEN}-> {self.recommendation}{RESET}"
        )


# ── Auditor ───────────────────────────────────────────────────────────────────

class Auditor:
    def __init__(self, verbose: bool = False, resolve_pins: bool = False):
        self.findings:        list[Finding] = []
        self.visited_actions: set[str]      = set()
        self.verbose      = verbose
        self.resolve_pins = resolve_pins

    def log(self, msg: str) -> None:
        if self.verbose:
            print(f"{DIM}[debug] {msg}{RESET}", file=sys.stderr)

    # ── Step-level checks ─────────────────────────────────────────────────────

    def check_step(self, step: dict, workflow_file: str, job_name: Optional[str],
                   context: str = "workflow") -> list[Finding]:
        findings: list[Finding] = []
        uses = step.get("uses", "")
        if not uses:
            return findings

        base   = action_base_name(uses)
        inputs = {k: str(v).strip() for k, v in (step.get("with") or {}).items()}

        # ── actions/cache (always caches) ─────────────────────────────────────
        if base == "actions/cache":
            findings.append(Finding(
                level="HIGH", action=uses, workflow=workflow_file, job=job_name,
                message=f"actions/cache explicitly used ({context}). Any cache restored here "
                        "could be poisoned before reaching a publish/deploy step.",
                recommendation="Remove or gate behind a non-publishing workflow. "
                               "Use `if: github.event_name != 'release'` at minimum.",
                cache_input="(always caches)", cache_value="N/A",
            ))
            return findings

        # ── actions/cache sub-actions ─────────────────────────────────────────
        if base in ("actions/cache/restore", "actions/cache/save"):
            findings.append(Finding(
                level="HIGH", action=uses, workflow=workflow_file, job=job_name,
                message=f"{base} used ({context}). Granular cache sub-actions carry the same "
                        "poisoning risk as actions/cache.",
                recommendation="Remove from publishing/deploy workflows entirely.",
                cache_input="(always caches)", cache_value="N/A",
            ))
            return findings

        # ── Known cache-aware setup actions ───────────────────────────────────
        if base in CACHE_AWARE_ACTIONS:
            meta          = CACHE_AWARE_ACTIONS[base]
            cache_input   = meta["input"]
            default_since = meta["default_on_since"]

            if cache_input is None:
                return findings

            if self.resolve_pins:
                _, ref_part = parse_uses(uses)
                if ref_part and not is_sha_pin(ref_part):
                    resolved, already_pinned, invalid, rtag = resolve_ref_to_sha(base, ref_part)
                    if invalid:
                        findings.append(Finding(
                            level="HIGH", action=uses, workflow=workflow_file, job=job_name,
                            message=f"Action ref `{ref_part}` is a wildcard or structurally "
                                    "invalid Git ref and can never be pinned.",
                            recommendation=f"Replace with a full commit SHA:\n"
                                           f"    uses: {base}@<commit-sha>  # {ref_part}",
                            resolved_sha=None, resolved_tag=rtag,
                        ))
                    elif not already_pinned:
                        sha_disp  = resolved[:12] + "..." if resolved != ref_part else "unknown"
                        tag_note  = f" (via tag {rtag})" if rtag else ""
                        findings.append(Finding(
                            level="MEDIUM", action=uses, workflow=workflow_file, job=job_name,
                            message=f"Action uses tag/branch `{ref_part}` "
                                    f"(resolved to {sha_disp}{tag_note}) "
                                    "rather than a full commit SHA.",
                            recommendation=f"Pin to the full SHA:\n"
                                           f"    uses: {base}@{resolved}  # {ref_part}",
                            resolved_sha=resolved, resolved_tag=rtag,
                        ))

            safe_pairs         = EXPLICIT_DISABLE.get(base, [(cache_input, "")])
            explicitly_disabled = any(
                inputs.get(inp, "__NOT_SET__") == val for inp, val in safe_pairs
            )

            if explicitly_disabled:
                if self.verbose:
                    findings.append(Finding(
                        level="INFO", action=uses, workflow=workflow_file, job=job_name,
                        message="Cache is explicitly disabled.",
                        recommendation="No action needed.",
                        cache_input=cache_input,
                        cache_value=inputs.get(cache_input, "(disable flag set)"),
                    ))
                return findings

            cache_val = inputs.get(cache_input, "__NOT_SET__")
            if cache_val == "__NOT_SET__":
                findings.append(Finding(
                    level="HIGH", action=uses, workflow=workflow_file, job=job_name,
                    message=f"Cache input `{cache_input}` is not set. "
                            f"Caching is ON by default since {default_since} "
                            f"when a lockfile is present ({context}).",
                    recommendation=f"Add `{cache_input}: ''` under `with:` to disable caching.",
                    cache_input=cache_input, cache_value="__NOT_SET__",
                ))
            else:
                findings.append(Finding(
                    level="MEDIUM", action=uses, workflow=workflow_file, job=job_name,
                    message=f"Cache input `{cache_input}` = `{cache_val}` -- "
                            f"verify this actually disables caching ({context}).",
                    recommendation=f"Confirm the correct disable value for {base}.",
                    cache_input=cache_input, cache_value=cache_val,
                ))
            return findings

        # ── Unknown / third-party -- recurse if composite ─────────────────────
        owner_repo, ref = parse_uses(uses)
        if owner_repo and ref:
            findings.extend(
                self.audit_composite(uses, owner_repo, ref, workflow_file, job_name)
            )

        return findings

    # ── Composite action recursion ────────────────────────────────────────────

    def audit_composite(self, uses: str, owner_repo: str, ref: str,
                        parent_workflow: str, job_name: Optional[str]) -> list[Finding]:
        findings:     list[Finding] = []
        resolved_ref: str           = ref

        if self.resolve_pins:
            resolved_ref, already_pinned, invalid, rtag = resolve_ref_to_sha(owner_repo, ref)
            if invalid:
                findings.append(Finding(
                    level="HIGH", action=uses, workflow=parent_workflow, job=job_name,
                    message=f"Action ref `{ref}` is a wildcard or structurally invalid Git ref.",
                    recommendation=f"Replace with a full commit SHA:\n"
                                   f"    uses: {owner_repo}@<commit-sha>  # {ref}",
                    resolved_sha=None, resolved_tag=rtag,
                ))
                return findings
            elif not already_pinned:
                if resolved_ref != ref:
                    tag_note = f" (via tag {rtag})" if rtag else ""
                    findings.append(Finding(
                        level="MEDIUM", action=uses, workflow=parent_workflow, job=job_name,
                        message=f"Action pinned to tag/branch `{ref}` "
                                f"(resolved to {resolved_ref[:12]}...{tag_note}).",
                        recommendation=f"Pin to the full SHA:\n"
                                       f"    uses: {owner_repo}@{resolved_ref}  # {ref}",
                        resolved_sha=resolved_ref, resolved_tag=rtag,
                    ))
                else:
                    findings.append(Finding(
                        level="MEDIUM", action=uses, workflow=parent_workflow, job=job_name,
                        message=f"Could not resolve `{ref}` to a SHA for {owner_repo}.",
                        recommendation="Manually verify and replace with a pinned commit SHA.",
                    ))
            self.log(
                f"Resolved {owner_repo}@{ref} -> {resolved_ref[:12]}... "
                f"(pinned={already_pinned}, invalid={invalid}, tag={rtag})"
            )

        cache_key = f"{owner_repo}@{resolved_ref}"
        if cache_key in self.visited_actions:
            return findings
        self.visited_actions.add(cache_key)

        self.log(f"Fetching {cache_key} ...")
        try:
            action_def = fetch_action_yml(owner_repo, resolved_ref)
        except Exception as exc:
            findings.append(Finding(
                level="MEDIUM", action=uses, workflow=parent_workflow, job=job_name,
                message=f"Could not fetch action.yml for {cache_key}: {exc}",
                recommendation="Manually inspect this action for caching behaviour.",
            ))
            return findings

        if not action_def:
            return findings

        runs = action_def.get("runs", {})
        if runs.get("using") != "composite":
            return findings

        inner: list[Finding] = []
        for step in (runs.get("steps") or []):
            inner.extend(
                self.check_step(step, parent_workflow, job_name,
                                context=f"composite:{uses}")
            )

        if inner:
            findings.append(Finding(
                level="HIGH", action=uses, workflow=parent_workflow, job=job_name,
                message="Composite action contains hidden caching (see sub-findings below).",
                recommendation="Pin to an audited SHA and ask the maintainer to expose "
                               "a `cache: ''` passthrough input.",
            ))
            findings.extend(inner)

        return findings

    # ── Workflow-level audit ──────────────────────────────────────────────────

    def audit_workflow(self, workflow_path: Path,
                       repo: Optional[str] = None) -> list[Finding]:
        findings: list[Finding] = []
        try:
            content = yaml.safe_load(workflow_path.read_text())
        except Exception as exc:
            print(f"{RED}Error parsing {workflow_path}: {exc}{RESET}")
            return findings

        if not isinstance(content, dict):
            return findings

        for job_name, job in (content.get("jobs") or {}).items():
            if not isinstance(job, dict):
                continue
            for step in (job.get("steps") or []):
                if not isinstance(step, dict):
                    continue
                sf = self.check_step(step, str(workflow_path), job_name)
                for f in sf:
                    if repo:
                        f.repo = repo
                self.findings.extend(sf)
                findings.extend(sf)

        return findings


# ── Terminal report ───────────────────────────────────────────────────────────

def print_report(all_findings: list[Finding], workflows: list[Path]) -> None:
    high   = [f for f in all_findings if f.level == "HIGH"]
    medium = [f for f in all_findings if f.level == "MEDIUM"]

    print(f"\n{BOLD}{'─'*60}{RESET}")
    print(f"{BOLD}GitHub Caching Audit{RESET}")
    print(f"{BOLD}{'─'*60}{RESET}")
    print(f"Workflows scanned : {len(workflows)}")
    print(f"Findings          : "
          f"{RED}{len(high)} HIGH{RESET}  "
          f"{YELLOW}{len(medium)} MEDIUM{RESET}")
    print()

    def _section(label: str, items: list[Finding], colour: str) -> None:
        if not items:
            return
        print(f"{colour}{BOLD}== {label} {'='*(54-len(label))}{RESET}")
        by_wf: dict[str, list[Finding]] = {}
        for f in items:
            by_wf.setdefault(f.workflow, []).append(f)
        for wf, fs in by_wf.items():
            print(f"\n  {DIM}{wf}{RESET}")
            for f in fs:
                print(str(f))
                print()

    _section("HIGH RISK", high, RED)
    _section("MEDIUM RISK", medium, YELLOW)

    if not high and not medium:
        print(f"{GREEN}No cache-related findings.{RESET}\n")

    if high or medium:
        print(f"{BOLD}{'─'*60}{RESET}")
        print(f"{BOLD}Recommended fixes for publishing workflows:{RESET}")
        print(textwrap.dedent(f"""
          {CYAN}actions/setup-node / setup-python / setup-java / setup-dotnet{RESET}:
            with:
              cache: ''

          {CYAN}actions/setup-go{RESET}:
            with:
              cache: false

          {CYAN}gradle/gradle-build-action{RESET}:
            with:
              cache-disabled: true
        """))


# ── HTML report ───────────────────────────────────────────────────────────────

def _h(text: str) -> str:
    """HTML-escape a string."""
    return (str(text)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&#39;"))


def _badge(level: str) -> str:
    colours = {
        "HIGH":   ("#c0392b", "#fff"),
        "MEDIUM": ("#e67e22", "#fff"),
        "INFO":   ("#2980b9", "#fff"),
        "PASS":   ("#27ae60", "#fff"),
    }
    bg, fg = colours.get(level, ("#7f8c8d", "#fff"))
    return (f'<span style="background:{bg};color:{fg};padding:2px 9px;border-radius:3px;'
            f'font-size:.72rem;font-weight:700;letter-spacing:.06em;'
            f'white-space:nowrap">{_h(level)}</span>')


def generate_html_report(all_findings: list[Finding], workflows: list[Path],
                         output_path: Path) -> None:
    now    = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    high   = [f for f in all_findings if f.level == "HIGH"]
    medium = [f for f in all_findings if f.level == "MEDIUM"]

    # ── Grouping helpers ──────────────────────────────────────────────────────

    def repo_key(f: Finding) -> str:
        return f.repo if f.repo else Path(f.workflow).name

    repos: dict[str, list[Finding]] = {}
    for f in all_findings:
        repos.setdefault(repo_key(f), []).append(f)
    for wf in workflows:
        slug = None
        for f in all_findings:
            if Path(f.workflow) == wf:
                slug = repo_key(f)
                break
        repos.setdefault(slug or wf.name, [])

    def risk_level(fs: list[Finding]) -> str:
        levels = {f.level for f in fs}
        if "HIGH"   in levels: return "HIGH"
        if "MEDIUM" in levels: return "MEDIUM"
        if levels:             return "INFO"
        return "PASS"

    def risk_colour(level: str) -> str:
        return {"HIGH": "#c0392b", "MEDIUM": "#e67e22",
                "INFO": "#2980b9", "PASS": "#27ae60"}.get(level, "#7f8c8d")

    def slug_id(s: str) -> str:
        import re as _re
        return _re.sub(r'[^a-zA-Z0-9_-]', '_', s)

    # ── BOARD ─────────────────────────────────────────────────────────────────
    overall = "HIGH" if high else ("MEDIUM" if medium else "PASS")
    overall_colour = risk_colour(overall)
    overall_text = {
        "HIGH":   "Critical cache hygiene issues detected. Immediate remediation required "
                  "before the next package publish or release.",
        "MEDIUM": "Potential cache risks identified. Review and pin all action refs "
                  "before the next release cycle.",
        "PASS":   "No cache hygiene issues detected across all scanned workflows.",
    }[overall]

    # Only include repos with actual findings (HIGH or MEDIUM)
    board_rows = ""
    for repo, fs in sorted(repos.items()):
        actionable = [f for f in fs if f.level in ("HIGH", "MEDIUM")]
        if not actionable:
            continue
        rl    = risk_level(actionable)
        col   = risk_colour(rl)
        h     = sum(1 for f in actionable if f.level == "HIGH")
        m     = sum(1 for f in actionable if f.level == "MEDIUM")
        rid   = slug_id(repo)
        verdict = (
            f"{h} critical issue{'s' if h != 1 else ''}, "
            f"{m} warning{'s' if m != 1 else ''}"
        )
        board_rows += f"""
        <tr id="board-row-{rid}">
          <td>
            <button class="collapse-btn" data-target="board-detail-{rid}"
                    aria-expanded="true" title="Collapse">&#x25BC;</button>
            <span class="mono">{_h(repo)}</span>
          </td>
          <td style="text-align:center">{_badge(rl)}</td>
          <td style="color:#555;font-size:.88rem">{_h(verdict)}</td>
        </tr>
        <tr id="board-detail-{rid}" class="detail-row">
          <td colspan="3" style="padding:0">
            <table class="inner-table">
              <thead><tr>
                <th>Workflow</th><th>Job</th><th>Level</th><th>Finding</th>
              </tr></thead>
              <tbody>{''.join(
                f'<tr>'
                f'<td class="mono" style="font-size:.78rem">{_h(Path(f.workflow).name)}</td>'
                f'<td style="font-size:.78rem;color:#666">{_h(f.job or "")}</td>'
                f'<td>{_badge(f.level)}</td>'
                f'<td style="font-size:.8rem">{_h(f.message[:120])}{"..." if len(f.message)>120 else ""}</td>'
                f'</tr>'
                for f in actionable
              )}</tbody>
            </table>
          </td>
        </tr>"""

    if not board_rows:
        board_rows = f"""
        <tr><td colspan="3" style="padding:18px;color:#27ae60;font-weight:600;text-align:center">
          No cache hygiene issues found across all scanned workflows.
        </td></tr>"""

    # ── ASSEMBLE ──────────────────────────────────────────────────────────────
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>GitHub Caching Audit &mdash; {_h(now)}</title>
<style>
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
     background:#f5f6fa;color:#2c3e50;line-height:1.6;font-size:15px}}
header{{background:#1a1f2e;color:#fff;padding:18px 40px;
        display:flex;align-items:center;justify-content:space-between;
        flex-wrap:wrap;gap:8px}}
header h1{{font-size:1.05rem;font-weight:700;letter-spacing:.04em}}
.meta{{font-size:.78rem;color:#8899aa}}
.container{{max-width:1220px;margin:0 auto;padding:30px 22px}}
h2{{font-size:1.3rem;font-weight:700;margin-bottom:5px;color:#1a1f2e}}
h3{{font-size:.95rem;font-weight:600;margin:22px 0 10px;color:#2c3e50}}
.lead{{color:#777;margin-bottom:22px;font-size:.92rem}}
/* verdict */
.verdict-box{{background:#fff;border-radius:7px;padding:18px 22px;
              margin-bottom:26px;box-shadow:0 1px 4px rgba(0,0,0,.07)}}
.verdict-label{{font-weight:800;font-size:1.05rem;display:block;
                margin-bottom:4px;letter-spacing:.06em}}
.verdict-meta{{font-size:.8rem;color:#888;margin-top:8px}}
/* stat row */
.stat-row{{display:flex;gap:14px;margin-bottom:28px;flex-wrap:wrap}}
.stat{{background:#fff;border-radius:7px;padding:16px 22px;border-top:3px solid;
       box-shadow:0 1px 4px rgba(0,0,0,.07);min-width:110px;text-align:center;flex:1}}
.stat-num{{font-size:1.9rem;font-weight:800;line-height:1}}
.stat-label{{font-size:.72rem;color:#999;margin-top:4px;
             text-transform:uppercase;letter-spacing:.07em}}
/* tables */
.data-table{{width:100%;border-collapse:collapse;background:#fff;
             border-radius:7px;overflow:hidden;
             box-shadow:0 1px 4px rgba(0,0,0,.07);font-size:.86rem}}
.data-table thead tr{{background:#1a1f2e;color:#fff}}
.data-table th{{padding:10px 14px;text-align:left;font-weight:600;
                font-size:.76rem;letter-spacing:.06em;text-transform:uppercase}}
.data-table td{{padding:9px 14px;border-bottom:1px solid #f0f2f4;vertical-align:top}}
.data-table tr:last-child td{{border-bottom:none}}
.data-table tbody tr:hover{{background:#fafbfc}}
.inner-table{{width:100%;border-collapse:collapse;font-size:.8rem;background:#f8f9fa}}
.inner-table th{{padding:7px 14px;background:#2c3e50;color:#fff;font-weight:600;
                 font-size:.73rem;letter-spacing:.05em;text-transform:uppercase;text-align:left}}
.inner-table td{{padding:7px 14px;border-bottom:1px solid #eee;vertical-align:top}}
.inner-table tr:last-child td{{border-bottom:none}}
/* collapsible */
.collapse-btn{{background:none;border:none;cursor:pointer;padding:2px 5px;
               color:#888;font-size:.85rem;line-height:1;border-radius:3px}}
.collapse-btn:hover{{background:#f0f0f0}}
.bulk-btns{{display:flex;gap:6px}}
.bulk-btn{{background:#fff;border:1px solid #ddd;border-radius:4px;
           padding:5px 12px;font-size:.78rem;cursor:pointer;color:#555;
           transition:background .15s}}
.bulk-btn:hover{{background:#f0f4f8;border-color:#bbb}}
/* board detail rows */
.detail-row td{{padding:0;background:#f8f9fa}}
.detail-row.collapsed{{display:none}}
/* misc */
.mono{{font-family:monospace;font-size:.83rem}}
code{{background:#f0f2f4;padding:1px 5px;border-radius:3px;
      font-size:.83em;font-family:monospace;word-break:break-all}}
@media(max-width:700px){{
  .stat-row{{flex-direction:column}}
  header{{padding:14px 18px}}
}}
</style>
</head>
<body>
<header>
  <h1>&#x1F512; GitHub Caching Audit</h1>
  <span class="meta">Generated {_h(now)} &middot; {len(workflows)} workflow(s)</span>
</header>
<div class="container">
  <div id="board">
    <h2>GitHub Caching Audit</h2>
    <p class="lead">Risk posture across all scanned repositories and workflows.</p>

    <div class="verdict-box" style="border-left:5px solid {overall_colour}">
      <span class="verdict-label" style="color:{overall_colour}">{_h(overall)}</span>
      <p>{_h(overall_text)}</p>
      <p class="verdict-meta">Audit run: <strong>{_h(now)}</strong>
         &nbsp;&middot;&nbsp; Workflows scanned: <strong>{len(workflows)}</strong>
         &nbsp;&middot;&nbsp; Repositories / sources: <strong>{len(repos)}</strong></p>
    </div>

    <div class="stat-row">
      <div class="stat" style="border-top-color:#c0392b">
        <div class="stat-num" style="color:#c0392b">{len(high)}</div>
        <div class="stat-label">Critical (HIGH)</div>
      </div>
      <div class="stat" style="border-top-color:#e67e22">
        <div class="stat-num" style="color:#e67e22">{len(medium)}</div>
        <div class="stat-label">Warnings (MEDIUM)</div>
      </div>
      <div class="stat" style="border-top-color:#27ae60">
        <div class="stat-num" style="color:#27ae60">{len(workflows)}</div>
        <div class="stat-label">Workflows</div>
      </div>
    </div>

    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px">
      <h3 style="margin:0">Per-Repository Risk Rating</h3>
      <div class="bulk-btns">
        <button class="bulk-btn" onclick="collapseAll()">Collapse all</button>
        <button class="bulk-btn" onclick="expandAll()">Expand all</button>
      </div>
    </div>
    <table class="data-table">
      <thead><tr>
        <th>Repository / Source</th>
        <th style="text-align:center">Risk</th>
        <th>Summary</th>
      </tr></thead>
      <tbody>{board_rows}</tbody>
    </table>
  </div>
</div>
<script>
// ── Board table row collapse ──────────────────────────────────────────────────
document.querySelectorAll('.collapse-btn').forEach(btn => {{
  btn.addEventListener('click', e => {{
    e.stopPropagation();
    const targetId = btn.dataset.target;
    const row      = document.getElementById(targetId);
    if (!row) return;
    const isNowCollapsed = row.classList.toggle('collapsed');
    btn.innerHTML        = isNowCollapsed ? '&#x25BA;' : '&#x25BC;';
    btn.title            = isNowCollapsed ? 'Expand' : 'Collapse';
  }});
}});

function collapseAll() {{
  document.querySelectorAll('.detail-row').forEach(r => r.classList.add('collapsed'));
  document.querySelectorAll('.collapse-btn').forEach(btn => {{
    btn.innerHTML = '&#x25BA;';
    btn.title     = 'Expand';
  }});
}}

function expandAll() {{
  document.querySelectorAll('.detail-row').forEach(r => r.classList.remove('collapsed'));
  document.querySelectorAll('.collapse-btn').forEach(btn => {{
    btn.innerHTML = '&#x25BC;';
    btn.title     = 'Collapse';
  }});
}}
</script>
</body>
</html>"""

    output_path.write_text(html, encoding="utf-8")
    print(f"{GREEN}HTML report written to {output_path}{RESET}")




# ── Remote fetch helper ───────────────────────────────────────────────────────

def fetch_remote_workflows(remote: str, tmpdir: Path) -> list[tuple[Path, str]]:
    """
    Fetch workflow files for a remote repo slug.
    Returns a list of (local_path, owner/repo_slug) tuples so the slug
    is always available for labelling — never inferred from the tmp path.
    """
    remote_slug = remote
    remote_ref: Optional[str] = None
    if "@" in remote_slug:
        remote_slug, remote_ref = remote_slug.split("@", 1)

    dest_dir = tmpdir / remote_slug.replace("/", os.sep)
    dest_dir.mkdir(parents=True, exist_ok=True)

    ref_param = f"?ref={remote_ref}" if remote_ref else ""
    api_url = (
        f"https://api.github.com/repos/{remote_slug}"
        f"/contents/.github/workflows{ref_param}"
    )
    try:
        entries = gh_api(api_url)
    except HTTPError as e:
        print(f"{RED}GitHub API error fetching {remote_slug!r}: {e}{RESET}")
        return []

    if not isinstance(entries, list):
        print(f"{RED}Unexpected response for {remote_slug!r} -- "
              "check the slug is correct and the token has access.{RESET}")
        return []

    fetched: list[tuple[Path, str]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if not entry["name"].endswith((".yml", ".yaml")):
            continue
        if remote_ref:
            raw_url = (
                f"https://raw.githubusercontent.com/{remote_slug}"
                f"/{remote_ref}/.github/workflows/{entry['name']}"
            )
        else:
            raw_url = entry["download_url"]
        dest = dest_dir / entry["name"]
        try:
            raw = gh_api(raw_url)
        except HTTPError as e:
            print(f"{RED}Failed to download {remote_slug}/{entry['name']}: {e}{RESET}")
            continue
        dest.write_text(raw if isinstance(raw, str) else json.dumps(raw))
        fetched.append((dest, remote_slug))

    return fetched


# ── CLI ───────────────────────────────────────────────────────────────────────

def collect_workflows(repo_path: Path) -> list[Path]:
    wf_dir = repo_path / ".github" / "workflows"
    if not wf_dir.exists():
        return []
    return sorted(wf_dir.glob("*.yml")) + sorted(wf_dir.glob("*.yaml"))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Audit GitHub Actions workflows for unsafe cache configuration.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Examples:
              python audit-actions-cache.py --workflow .github/workflows/publish.yml
              python audit-actions-cache.py --repo-path /path/to/local/repo
              GITHUB_TOKEN=ghp_... python audit-actions-cache.py --remote owner/repo@v2.23.0
              GITHUB_TOKEN=ghp_... python audit-actions-cache.py --remotes-file repos.yml
              GITHUB_TOKEN=ghp_... python audit-actions-cache.py --remotes-file repos.yml --html report.html
              GITHUB_TOKEN=ghp_... python audit-actions-cache.py \\
                  --action untitaker/hyperlink@fb5bb9c5011a3d143a54b4b30aedc30ec5bc0f89 \\
                  --action testlens-app/setup-testlens@*

            repos.yml format:
              - pypa/cibuildwheel@v2.23.0
              - psf/black
              - astral-sh/uv@8d2b08b68458a16aeb24b64e68a09ab1c8e82084
              - some-org/some-action@*   # resolves to latest release
        """),
    )
    parser.add_argument("--workflow",   help="Path to a single workflow file")
    parser.add_argument("--repo-path",  help="Path to a local repo root")
    parser.add_argument("--remote",
                        help="GitHub repo slug: owner/repo or owner/repo@ref "
                             "-- requires GITHUB_TOKEN")
    parser.add_argument(
        "--remotes-file", metavar="FILE", dest="remotes_file",
        help="YAML file listing repo slugs to audit (owner/repo or owner/repo@ref). "
             "Use @* to resolve to latest release.",
    )
    parser.add_argument(
        "--action", metavar="OWNER/REPO@REF", action="append", dest="actions",
        help="Audit a specific action ref. Use @* for latest release. Repeatable.",
    )
    parser.add_argument("--html",    metavar="FILE", default="audit-report.html",
                        help="Path for the HTML report (default: audit-report.html)")
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--json",    action="store_true",
                        help="Output findings as JSON instead of terminal report")
    args = parser.parse_args()

    if not any([args.workflow, args.repo_path, args.remote, args.remotes_file, args.actions]):
        args.repo_path = "."

    resolve = bool(args.remote or args.remotes_file or args.actions)
    auditor = Auditor(verbose=args.verbose, resolve_pins=resolve)
    # Each entry is (workflow_path, repo_slug_or_None)
    workflow_tuples: list[tuple[Path, Optional[str]]] = []

    if args.workflow:
        workflow_tuples = [(Path(args.workflow), None)]

    elif args.repo_path:
        local_wfs = collect_workflows(Path(args.repo_path))
        if not local_wfs:
            print(f"{YELLOW}No workflow files found under "
                  f"{args.repo_path}/.github/workflows/{RESET}")
            sys.exit(0)
        workflow_tuples = [(wf, None) for wf in local_wfs]

    elif args.remote or args.remotes_file:
        if not github_token():
            print(f"{RED}GITHUB_TOKEN is required for --remote / --remotes-file{RESET}")
            sys.exit(1)

        import tempfile
        tmpdir = Path(tempfile.mkdtemp())

        # workflow_tuples already declared above
        remotes: list[str] = []
        if args.remote:
            remotes.append(args.remote)
        if args.remotes_file:
            rfile = Path(args.remotes_file)
            if not rfile.exists():
                print(f"{RED}--remotes-file not found: {rfile}{RESET}")
                sys.exit(1)
            try:
                loaded = yaml.safe_load(rfile.read_text())
            except Exception as e:
                print(f"{RED}Failed to parse --remotes-file: {e}{RESET}")
                sys.exit(1)
            if not isinstance(loaded, list) or not all(isinstance(s, str) for s in loaded):
                print(f"{RED}--remotes-file must be a YAML list of strings, e.g.:\n"
                      f"  - owner/repo\n  - owner/repo@ref{RESET}")
                sys.exit(1)
            remotes.extend(loaded)

        if not remotes:
            print(f"{YELLOW}No remote slugs to audit.{RESET}")
            sys.exit(0)

        for remote in remotes:
            print(f"{DIM}Fetching workflows for {remote} ...{RESET}", file=sys.stderr)
            workflow_tuples.extend(fetch_remote_workflows(remote, tmpdir))

    elif args.actions:
        if not github_token():
            print(f"{RED}GITHUB_TOKEN is required for --action{RESET}")
            sys.exit(1)

        import tempfile
        tmpdir       = Path(tempfile.mkdtemp())
        synthetic_wf = tmpdir / "_direct_action_audit.yml"

        steps = []
        for action_ref in args.actions:
            owner_repo, ref = parse_uses(action_ref)
            if not owner_repo:
                print(f"{YELLOW}Skipping unparseable ref: {action_ref!r}{RESET}",
                      file=sys.stderr)
                continue
            steps.append({"uses": action_ref})

        if not steps:
            print(f"{RED}No valid action refs to audit.{RESET}")
            sys.exit(1)

        synthetic = {
            "on": "workflow_dispatch",
            "jobs": {"audit": {"runs-on": "ubuntu-latest", "steps": steps}},
        }
        synthetic_wf.write_text(yaml.dump(synthetic))
        workflow_tuples = [(synthetic_wf, None)]

    all_findings: list[Finding] = []
    workflows: list[Path] = []
    for wf, slug in workflow_tuples:
        print(f"{DIM}Auditing {wf} ...{RESET}", file=sys.stderr)
        all_findings.extend(auditor.audit_workflow(wf, repo=slug))
        workflows.append(wf)

    if args.json:
        print(json.dumps([
            {
                "level": f.level, "action": f.action, "workflow": f.workflow,
                "job": f.job, "message": f.message, "recommendation": f.recommendation,
                "resolved_sha": f.resolved_sha, "resolved_tag": f.resolved_tag,
                "cache_input": f.cache_input, "cache_value": f.cache_value,
                "repo": f.repo,
            }
            for f in all_findings
        ], indent=2))
    else:
        print_report(all_findings, workflows)

    if args.html:
        generate_html_report(all_findings, workflows, Path(args.html))

    sys.exit(1 if any(f.level == "HIGH" for f in all_findings) else 0)


if __name__ == "__main__":
    main()
