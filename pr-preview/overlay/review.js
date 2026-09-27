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

(function () {
  "use strict";
  var cfg = window.__ASF_PREVIEW__;
  if (!cfg || !cfg.repo || !cfg.pr) return; // not a preview

  var armed = false;
  var drag = null;
  var root, box, button, toast, banner, mark, panel;

  function el(tag, style, text) {
    var n = document.createElement(tag);
    n.style.cssText = style;
    if (text) n.textContent = text;
    return n;
  }

  function say(message, ms) {
    toast.textContent = message;
    toast.style.display = "block";
    clearTimeout(say._t);
    say._t = setTimeout(function () { toast.style.display = "none"; }, ms || 6000);
  }

  // root holds the dimming and the marking box; button and toast are siblings
  // on document.body, so hiding root alone leaves them in the captured image.
  function hideChrome() {
    root.style.display = "none";
    button.style.display = "none";
    toast.style.display = "none";
    banner.style.display = "none";
    hideResult();
  }

  function showChrome() {
    button.style.display = "";
    banner.style.display = "";
  }

  function sourceUnder(x, y) {
    var node = document.elementFromPoint(x, y);
    while (node && node !== document.body) {
      if (node.getAttribute && node.getAttribute("data-preview-src")) {
        return node.getAttribute("data-preview-src");
      }
      node = node.parentElement;
    }
    return null;
  }

  function hideResult() {
    mark.style.display = "none";
    panel.style.display = "none";
    panel.textContent = "";
  }

  // The pull request opens from a link the reviewer clicks, not from
  // window.open: a popup fired after an awaited clipboard write has lost the
  // click's activation and is blocked, and jumping tabs unasked also hides the
  // region the reviewer just marked. Leaving the region outlined with the
  // result beside it lets them see what was captured before they go.
  function showResult(region, url, source, message) {
    mark.style.left = region.x + "px"; mark.style.top = region.y + "px";
    mark.style.width = region.w + "px"; mark.style.height = region.h + "px";
    mark.style.display = "block";

    panel.textContent = "";
    panel.appendChild(el("div", "margin-bottom:8px", message));
    if (source) {
      panel.appendChild(el("div", "margin-bottom:8px;color:#94a3b8;font:12px ui-monospace,monospace", source));
    }

    var go = document.createElement("a");
    go.href = url;
    go.target = "_blank";
    go.rel = "noopener";
    go.style.cssText =
      "display:inline-block;margin-right:8px;padding:6px 12px;border-radius:6px;" +
      "background:#e11d48;color:#fff;text-decoration:none;font:600 13px system-ui";
    go.textContent = "Open PR #" + cfg.pr + (source ? " at this line" : "") + " \u2197";
    go.addEventListener("click", function () { setTimeout(hideResult, 0); });
    panel.appendChild(go);

    var done = el("button",
      "padding:6px 10px;border-radius:6px;border:1px solid #334155;background:transparent;" +
      "color:#e2e8f0;font:13px system-ui;cursor:pointer", "Dismiss");
    done.addEventListener("click", hideResult);
    panel.appendChild(done);

    // Beside the region where it fits: below, else above, else inside it.
    panel.style.visibility = "hidden";
    panel.style.display = "block";
    var ph = panel.offsetHeight, pw = panel.offsetWidth;
    var top = region.y + region.h + 8;
    if (top + ph > window.innerHeight - 8) top = region.y - ph - 8;
    if (top < 8) top = Math.max(8, Math.min(region.y + 8, window.innerHeight - ph - 8));
    var left = Math.max(8, Math.min(region.x, window.innerWidth - pw - 8));
    panel.style.top = top + "px";
    panel.style.left = left + "px";
    panel.style.visibility = "";
    go.focus();
  }

  function disarm() {
    armed = false;
    drag = null;
    root.style.display = "none";
    box.style.display = "none";
    button.textContent = "Comment on this preview";
  }

  async function submit(region) {
    // Hide the overlay BEFORE resolving the source. root is
    // position:fixed;inset:0 and must accept pointer events to receive the
    // drag, so with it displayed elementFromPoint returns root itself, the walk
    // ends at <body>, and the source is never resolved — which silently turns
    // every capture into a Conversation-tab fallback.
    hideChrome();

    var source = sourceUnder(region.x + region.w / 2, region.y + region.h / 2);
    var url = targetUrl({ repo: cfg.repo, pr: cfg.pr, source: source, anchors: cfg.anchors });
    var caption = captionFor({
      url: location.href,
      source: source,
      region: region,
      sha: cfg.sha,
    });

    // Built as a promise and handed straight to ClipboardItem, so
    // clipboard.write() is reached while the click's transient activation is
    // still valid. Awaiting the capture first loses it, and Safari then refuses
    // the write on every large page.
    var blobPromise = (async function () {
      // html2canvas-pro's UMD bundle exposes a module namespace, not a
      // callable: window.html2canvas is an object whose .default is the
      // function. The older html2canvas exposed the function directly, so
      // resolve both shapes rather than depending on one.
      var capture =
        (window.html2canvas && (window.html2canvas.default || window.html2canvas.html2canvas)) ||
        window.html2canvas;
      if (typeof capture !== "function") {
        throw new Error("the screenshot library did not load");
      }

      var shot = await capture(document.body, {
        x: window.scrollX, y: window.scrollY,
        width: window.innerWidth, height: window.innerHeight,
        scale: Math.min(window.devicePixelRatio || 1, 2),
        useCORS: true, logging: false,
      });

      var scale = shot.width / window.innerWidth;
      var out = document.createElement("canvas");
      out.width = shot.width;
      out.height = shot.height + 28 * scale;
      var ctx = out.getContext("2d");

      ctx.drawImage(shot, 0, 0);
      ctx.fillStyle = "rgba(15,23,42,0.55)";
      ctx.fillRect(0, 0, out.width, region.y * scale);
      ctx.fillRect(0, (region.y + region.h) * scale, out.width, shot.height);
      ctx.fillRect(0, region.y * scale, region.x * scale, region.h * scale);
      ctx.fillRect((region.x + region.w) * scale, region.y * scale, out.width, region.h * scale);
      ctx.strokeStyle = "#e11d48";
      ctx.lineWidth = 2 * scale;
      ctx.strokeRect(region.x * scale, region.y * scale, region.w * scale, region.h * scale);

      ctx.fillStyle = "#0f172a";
      ctx.fillRect(0, shot.height, out.width, 28 * scale);
      ctx.fillStyle = "#e2e8f0";
      ctx.font = (13 * scale) + "px ui-monospace, monospace";

      var text = caption;
      while (text.length > 12 && ctx.measureText(text).width > out.width - 16 * scale) {
        text = text.slice(0, -4) + "…";
      }
      ctx.fillText(text, 8 * scale, shot.height + 19 * scale);

      // toBlob throws SecurityError on a canvas tainted by a cross-origin
      // image. Inside this promise it surfaces as a rejection and is reported,
      // rather than escaping a callback and leaving the overlay stuck.
      return await new Promise(function (resolve, reject) {
        try {
          out.toBlob(function (blob) {
            if (blob) resolve(blob);
            else reject(new Error("the canvas produced no image"));
          }, "image/png");
        } catch (err) {
          reject(err);
        }
      });
    })();

    var copied = false;
    try {
      await navigator.clipboard.write([new ClipboardItem({ "image/png": blobPromise })]);
      copied = true;
    } catch (err) {
      copied = false;
    }

    showChrome();

    if (!copied) {
      // Either the clipboard refused, or the capture itself failed. Awaiting
      // the promise tells us which, and reports the real reason either way.
      var blob = null;
      try {
        blob = await blobPromise;
      } catch (err) {
        root.style.display = "block";
        say("Could not capture the page: " + err.message);
        return;
      }

      var a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = "preview-pr" + cfg.pr + ".png";
      a.click();
      disarm();
      showResult(region, url, source,
        "Clipboard refused \u2014 the screenshot was downloaded; drag it into the comment box");
    } else {
      disarm();
      showResult(region, url, source,
        "Screenshot copied \u2014 paste it into the comment box");
    }
  }

  function build() {
    button = el("button",
      "position:fixed;right:16px;bottom:16px;z-index:2147483646;padding:8px 12px;" +
      "border-radius:8px;border:1px solid #334155;background:#0f172a;color:#e2e8f0;" +
      "font:13px system-ui;cursor:pointer", "Comment on this preview");
    button.addEventListener("click", function () {
      hideResult();
      armed = !armed;
      root.style.display = armed ? "block" : "none";
      button.textContent = armed ? "Cancel (Esc)" : "Comment on this preview";
    });

    // Always on, and deliberately not dismissible: someone sent this URL to
    // someone else, and the reader needs to know it is a pull request's
    // preview and not the project's published site.
    banner = document.createElement("a");
    banner.href = "https://github.com/" + cfg.repo + "/pull/" + cfg.pr;
    banner.target = "_blank";
    banner.rel = "noopener";
    banner.style.cssText =
      "position:fixed;top:0;right:16px;z-index:2147483646;padding:4px 10px;" +
      "border-radius:0 0 6px 6px;background:#b45309;color:#fff;text-decoration:none;" +
      "font:12px/1.6 system-ui;box-shadow:0 1px 4px rgba(0,0,0,.3)";
    banner.textContent =
      "Preview of " + cfg.repo + " #" + cfg.pr + " · " + cfg.sha + " · not the published site";

    root = el("div", "position:fixed;inset:0;z-index:2147483645;display:none;cursor:crosshair");
    box = el("div", "position:absolute;border:2px solid #e11d48;background:rgba(225,29,72,0.08);display:none");
    root.appendChild(box);

    // Outlines the captured region after submit; the spread shadow dims the
    // rest of the page. pointer-events:none keeps the page usable meanwhile.
    mark = el("div",
      "position:fixed;z-index:2147483645;display:none;pointer-events:none;" +
      "border:2px solid #e11d48;box-shadow:0 0 0 100vmax rgba(15,23,42,0.35)");

    panel = el("div",
      "position:fixed;z-index:2147483647;display:none;max-width:min(420px,calc(100vw - 16px));" +
      "padding:10px 12px;border-radius:8px;background:#0f172a;color:#e2e8f0;" +
      "font:13px system-ui;box-shadow:0 4px 16px rgba(0,0,0,.35)");

    toast = el("div",
      "position:fixed;left:16px;bottom:16px;z-index:2147483647;display:none;max-width:60vw;" +
      "padding:8px 12px;border-radius:8px;background:#0f172a;color:#e2e8f0;font:13px system-ui");

    root.addEventListener("mousedown", function (e) {
      drag = { x1: e.clientX, y1: e.clientY, x2: e.clientX, y2: e.clientY };
      box.style.display = "block";
    });
    root.addEventListener("mousemove", function (e) {
      if (!drag) return;
      drag.x2 = e.clientX; drag.y2 = e.clientY;
      var r = clampRegion(drag, { w: window.innerWidth, h: window.innerHeight }) ||
              { x: Math.min(drag.x1, drag.x2), y: Math.min(drag.y1, drag.y2), w: 0, h: 0 };
      box.style.left = r.x + "px"; box.style.top = r.y + "px";
      box.style.width = r.w + "px"; box.style.height = r.h + "px";
    });
    root.addEventListener("mouseup", function () {
      if (!drag) return;
      var region = clampRegion(drag, { w: window.innerWidth, h: window.innerHeight });
      drag = null;
      box.style.display = "none";
      if (!region) { say("That region is too small — drag a box around what you mean"); return; }
      submit(region);
    });

    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && armed) disarm();
      else if (e.key === "Escape" && panel.style.display !== "none") hideResult();
      if (
        e.key === "c" &&
        !armed &&
        !e.metaKey && !e.ctrlKey && !e.altKey &&
        e.target === document.body
      ) {
        button.click();
      }
    });

    // A mouseup outside the window never reaches root, which would leave drag
    // set and make the next mousemove resize a box the user never started.
    window.addEventListener("mouseup", function () {
      if (!drag) return;
      drag = null;
      box.style.display = "none";
    });
    window.addEventListener("blur", function () {
      drag = null;
      box.style.display = "none";
    });

    document.body.appendChild(banner);
    document.body.appendChild(root);
    document.body.appendChild(mark);
    document.body.appendChild(panel);
    document.body.appendChild(button);
    document.body.appendChild(toast);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", build);
  } else {
    build();
  }
})();
