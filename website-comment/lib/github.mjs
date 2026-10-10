// Licensed to the Apache Software Foundation (ASF) under one
// or more contributor license agreements.  See the NOTICE file
// distributed with this work for additional information
// regarding copyright ownership.  The ASF licenses this file
// to you under the Apache License, Version 2.0 (the
// "License"); you may not use this file except in compliance
// with the License.  You may obtain a copy of the License at
//
//   http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing,
// software distributed under the License is distributed on an
// "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
// KIND, either express or implied.  See the License for the
// specific language governing permissions and limitations
// under the License.

const API = "https://api.github.com";

/**
 * The workflow whose successful runs carry the preview-site artifact. Another
 * site sets PREVIEW_BUILD_WORKFLOW; it is interpolated into an API path, so it
 * is held to a bare workflow file name.
 */
export function buildWorkflow(env = process.env) {
  const name = env.PREVIEW_BUILD_WORKFLOW || "build.yml";
  if (!/^[A-Za-z0-9_.-]+\.ya?ml$/.test(name) || name.startsWith(".")) {
    throw new TypeError(
      `PREVIEW_BUILD_WORKFLOW must be a workflow file name, got ${JSON.stringify(name)}`,
    );
  }
  return name;
}

/*
 * What the privileged publisher may learn about a pull request.
 *
 * Every read below passes GitHub's response through one of these projections
 * before any other code sees it, so a PR's title, description, branch name,
 * commit messages, diff and discussion never reach the publisher — not even as
 * values it ignores. The allowed fields, and why each is needed:
 *
 *   PR number                 identifies the PR and its preview
 *   label names               the arming state
 *   head SHA (40-hex)         binds the build artifact to the PR's current head
 *   author login + type       skips the explainer on bot-authored PRs
 *   comment id/author/type    arming commands and the publisher's own markers;
 *   comment body              ONLY when it is exactly the /show-preview command
 *                             or authored by a bot — never human discussion
 *   label-event actor         who armed it, to check write access
 *
 * The one thing taken from a pull request beyond this is the built site, which
 * comes as the unprivileged build's artifact and is screened, never executed.
 * github.test.mjs pins the projections and the set of reachable endpoints.
 */
const SHA_RE = /^[0-9a-f]{40}$/;
const str = (v) => (typeof v === "string" ? v : null);
const int = (v) => (Number.isInteger(v) ? v : null);

export const projectPull = (p) => ({
  number: int(p?.number),
  state: str(p?.state),
  labels: Array.isArray(p?.labels) ? p.labels.map((l) => ({ name: str(l?.name) })) : [],
  head: { sha: SHA_RE.test(String(p?.head?.sha)) ? p.head.sha : null },
  user: { login: str(p?.user?.login), type: str(p?.user?.type) },
});

/**
 * A pull request opened from a branch of THIS repository, for the head-branch
 * reap. The branch name is this repository's own ref, compared against its own
 * branch list; it is never interpolated into a path or a shell.
 */
export const projectHeadPull = (p) => ({
  number: int(p?.number),
  state: str(p?.state),
  head: {
    ref: str(p?.head?.ref),
    sha: SHA_RE.test(String(p?.head?.sha)) ? p.head.sha : null,
    repo: { full_name: str(p?.head?.repo?.full_name) },
  },
});

const COMMAND_RE = /^\s*\/show-preview\s*$/;
export const projectComment = (c) => {
  const type = str(c?.user?.type);
  const body = str(c?.body);
  return {
    id: int(c?.id),
    user: { login: str(c?.user?.login), type },
    body: body !== null && (type === "Bot" || COMMAND_RE.test(body)) ? body : null,
  };
};

export const projectLabelEvent = (e) => ({
  event: str(e?.event),
  label: { name: str(e?.label?.name) },
  actor: { login: str(e?.actor?.login) },
});

export const projectBranch = (b) => ({
  name: str(b?.name),
  protected: b?.protected === true,
  commit: { sha: str(b?.commit?.sha) },
});

export function createClient({ repo, token, fetchImpl = fetch, workflow = buildWorkflow() }) {
  async function request(path, { method = "GET", body } = {}) {
    const res = await fetchImpl(`${API}${path}`, {
      method,
      headers: {
        authorization: `Bearer ${token}`,
        accept: "application/vnd.github+json",
        "x-github-api-version": "2022-11-28",
        ...(body ? { "content-type": "application/json" } : {}),
      },
      ...(body ? { body: JSON.stringify(body) } : {}),
    });
    if (!res.ok) {
      const err = new Error(`${method} ${path} -> ${res.status}`);
      err.status = res.status;
      throw err;
    }
    // DELETE answers 204 with no body, and parsing that throws.
    if (res.status === 204) return null;
    return res.json();
  }

  /**
   * Follows pages until a short page arrives. An incomplete read here is not
   * cosmetic: a /show-preview comment missed on page 2 makes a PR look unarmed,
   * and an unarmed PR with a live preview gets tombstoned.
   */
  async function paginate(path) {
    const out = [];
    for (let page = 1; ; page += 1) {
      const sep = path.includes("?") ? "&" : "?";
      const batch = await request(`${path}${sep}per_page=100&page=${page}`);
      if (!Array.isArray(batch) || batch.length === 0) break;
      out.push(...batch);
      if (batch.length < 100) break;
    }
    return out;
  }

  return {
    listOpenPulls: async () =>
      (await paginate(`/repos/${repo}/pulls?state=open`)).map(projectPull),
    listComments: async (n) =>
      (await paginate(`/repos/${repo}/issues/${n}/comments`)).map(projectComment),

    async hasWriteAccess(login) {
      try {
        const r = await request(`/repos/${repo}/collaborators/${login}/permission`);
        return r.permission === "write" || r.permission === "admin";
      } catch (e) {
        if (e.status === 403 || e.status === 404) return false;
        throw e;
      }
    },

    async upsertComment(n, marker, body) {
      // Two conditions, both required. The marker must be the exact trailing
      // HTML comment — a substring test lets anyone plant it — and the author
      // must be a Bot, so a human comment can never be PATCHed out from under
      // its author while still showing their name.
      const tag = `<!-- ${marker} -->`;
      const withMarker = `${body}\n\n${tag}`;

      const existing = (await paginate(`/repos/${repo}/issues/${n}/comments`)).map(projectComment).find(
        (c) =>
          c?.user?.type === "Bot" &&
          typeof c.body === "string" &&
          c.body.trimEnd().endsWith(tag),
      );

      if (existing) {
        return request(`/repos/${repo}/issues/comments/${existing.id}`, {
          method: "PATCH",
          body: { body: withMarker },
        });
      }
      return request(`/repos/${repo}/issues/${n}/comments`, {
        method: "POST",
        body: { body: withMarker },
      });
    },

    /**
     * A new comment, never an edit. For events people should be notified of —
     * an edit to an existing comment notifies nobody.
     */
    createComment: (n, body) =>
      request(`/repos/${repo}/issues/${n}/comments`, { method: "POST", body: { body } }),

    /** Create the label if the repository does not have it yet. */
    async ensureLabel(name) {
      try {
        await request(`/repos/${repo}/labels/${encodeURIComponent(name)}`);
      } catch (e) {
        if (e.status !== 404) throw e;
        await request(`/repos/${repo}/labels`, {
          method: "POST",
          body: { name, color: "0e8a16", description: "Publish a live staging preview of this pull request" },
        });
      }
    },

    addLabel: (n, name) =>
      request(`/repos/${repo}/issues/${n}/labels`, { method: "POST", body: { labels: [name] } }),

    /** The pull request's labeled / unlabeled events, oldest first. */
    async listLabelEvents(n) {
      const events = await paginate(`/repos/${repo}/issues/${n}/events`);
      return events
        .filter((e) => e?.event === "labeled" || e?.event === "unlabeled")
        .map(projectLabelEvent);
    },

    /** Whether `login` has already left a `content` reaction on a comment. */
    async hasReaction(commentId, content, login) {
      const reactions = await paginate(
        `/repos/${repo}/issues/comments/${commentId}/reactions?content=${content}`,
      );
      return reactions.some((r) => str(r?.user?.login) === login);
    },

    addReaction: (commentId, content) =>
      request(`/repos/${repo}/issues/comments/${commentId}/reactions`, {
        method: "POST",
        body: { content },
      }),

    async listPreviewBranches() {
      const refs = await paginate(`/repos/${repo}/git/matching-refs/heads/preview/`);
      return refs.map((r) => r.ref.replace("refs/heads/", ""));
    },

    deleteBranch: (name) =>
      request(`/repos/${repo}/git/refs/heads/${name}`, { method: "DELETE" }),

    /** Every branch, with its tip SHA and protection flag. */
    listBranches: async () => (await paginate(`/repos/${repo}/branches`)).map(projectBranch),

    /** Pull requests in any state whose head is this repository's `branch`. */
    listPullsForHead: async (branch) =>
      (
        await paginate(
          `/repos/${repo}/pulls?state=all&head=${encodeURIComponent(`${repo.split("/")[0]}:${branch}`)}`,
        )
      ).map(projectHeadPull),

    /**
     * The newest successful build of this commit, as { id } only. A workflow
     * run object also carries the PR's branch name, title and head commit
     * message, none of which the publisher may see.
     */
    async latestSuccessfulBuild(headSha) {
      if (!SHA_RE.test(String(headSha))) throw new TypeError(`bad head SHA ${JSON.stringify(headSha)}`);
      const runs = await request(
        `/repos/${repo}/actions/workflows/${workflow}/runs?head_sha=${headSha}&status=success&per_page=1`,
      );
      const id = int(runs.workflow_runs?.[0]?.id);
      return id === null ? null : { id };
    },

    /** A build run's artifacts, as { id, name, expired } only. */
    async listRunArtifacts(runId) {
      const list = await request(`/repos/${repo}/actions/runs/${int(runId)}/artifacts`);
      return (list.artifacts ?? []).map((a) => ({
        id: int(a?.id),
        name: str(a?.name),
        expired: a?.expired === true,
      }));
    },

    artifactZipUrl: (artifactId) =>
      `${API}/repos/${repo}/actions/artifacts/${artifactId}/zip`,

    async branchHeadMessage(branch) {
      try {
        const commit = await request(`/repos/${repo}/commits/${encodeURIComponent(branch)}`);
        return commit?.commit?.message ?? "";
      } catch (e) {
        if (e.status === 404) return "";
        throw e;
      }
    },
  };
}
