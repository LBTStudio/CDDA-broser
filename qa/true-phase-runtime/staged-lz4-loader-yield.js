/*
 * Local QA only: serialize LZ4 virtual-FS registration for staged data packages.
 * Every package retains Emscripten's LZ4 node type and byte ranges.  The hook
 * changes only node-creation scheduling, not data content or paths.
 */
(function installStagedLz4RegistrationYield() {
  "use strict";

  const FRAME_BUDGET_MS = 12;
  const PRE_RUN_DEPENDENCY = "cdda_staged_lz4_registration";
  const queue = [];
  let running = false;

  function trace(stage, detail) {
    try { console.info("[CDDA lz4] " + stage, detail || ""); } catch (_) {}
  }
  let preRunDependencyHeld = false;

  function now() {
    return (typeof performance !== "undefined" && performance.now) ? performance.now() : Date.now();
  }

  function state() {
    if (!window.CDDA_STAGED_REGISTRATION) {
      window.CDDA_STAGED_REGISTRATION = { completed: [], active: null, failed: null };
    }
    return window.CDDA_STAGED_REGISTRATION;
  }

  function labelFor(pack) {
    return window.__cddaExpectedStagedPackage ||
      (pack && pack.metadata && pack.metadata.files && pack.metadata.files[0] &&
       ("package:" + pack.metadata.files[0].filename)) || "unknown-package";
  }

  function updateStatus(active) {
    const element = document.getElementById("loading-message");
    if (!element || Module.calledRun) return;
    element.textContent = "ゲームファイルを準備しています… (" + active.completed + "/" + active.total + ")";
  }

  Module.preRun = Module.preRun || [];
  Module.preRun.push(function installWhenRuntimeIsReady() {
    const lz4 = Module.LZ4;
    const fs = Module.FS;
    trace("install", { hasLz4: !!lz4, hasFs: !!fs });
    if (!lz4 || !fs || typeof lz4.loadPackage !== "function") {
      throw new Error("LZ4/FS was not available before staged package registration");
    }
    if (lz4.__cddaStagedYieldInstalled) return;
    lz4.__cddaStagedYieldInstalled = true;
    const originalLoadPackage = lz4.loadPackage.bind(lz4);

    function dispatch(type, detail) {
      window.dispatchEvent(new CustomEvent(type, { detail: detail }));
    }

    function prepareCompressedData(compressedData) {
      lz4.init();
      if (!Array.isArray(compressedData.cachedIndexes) || !Array.isArray(compressedData.cachedChunks) ||
          compressedData.cachedIndexes.length !== compressedData.cachedChunks.length) {
        throw new Error("Invalid LZ4 cache metadata");
      }
      for (let i = 0; i < compressedData.cachedIndexes.length; i++) {
        compressedData.cachedIndexes[i] = -1;
        compressedData.cachedChunks[i] = compressedData.data.subarray(
          compressedData.cachedOffset + i * lz4.CHUNK_SIZE,
          compressedData.cachedOffset + (i + 1) * lz4.CHUNK_SIZE
        );
      }
    }

    function registerOne(pack, file) {
      const cut = file.filename.lastIndexOf("/");
      const dir = cut > 0 ? file.filename.slice(0, cut) : "/";
      const name = file.filename.slice(cut + 1);
      fs.createPath("", dir, true, true);
      const parent = fs.analyzePath(dir).object;
      lz4.createNode(parent, name, lz4.FILE_MODE, 0, {
        compressedData: pack.compressedData,
        start: file.start,
        end: file.end,
      });
    }

    function finishCurrent(active) {
      const shared = state();
      shared.completed.push({ group: active.group, files: active.total, elapsedMs: Math.round(now() - active.startedAt) });
      trace("complete", { group: active.group, files: active.total });
      shared.active = null;
      dispatch("cdda-staged-package-complete", { group: active.group, files: active.total });
      running = false;
      if (queue.length) {
        requestAnimationFrame(startNext);
      } else if (preRunDependencyHeld) {
        preRunDependencyHeld = false;
        Module.removeRunDependency(PRE_RUN_DEPENDENCY);
      }
    }

    function failCurrent(active, error) {
      const shared = state();
      shared.failed = String(error && error.stack || error);
      trace("failed", { group: active.group, error: shared.failed });
      shared.active = null;
      console.error("Staged LZ4 node registration failed", error);
      dispatch("cdda-staged-package-failed", { group: active.group, error: shared.failed });
      // A failed pre-runtime registration intentionally retains the dependency:
      // starting C++ with a partial filesystem would conceal the root cause.
      running = false;
    }

    function startNext() {
      if (running || !queue.length) return;
      running = true;
      const request = queue.shift();
      const files = request.pack.metadata.files;
      trace("start", { group: request.group, files: files.length, queued: queue.length });
      const active = {
        group: request.group,
        pack: request.pack,
        files: files,
        total: files.length,
        completed: 0,
        startedAt: now(),
      };
      state().active = { group: active.group, total: active.total, completed: 0 };
      try {
        prepareCompressedData(active.pack.compressedData);
      } catch (error) {
        failCurrent(active, error);
        return;
      }

      function frame() {
        const frameStarted = now();
        try {
          while (active.completed < active.total && now() - frameStarted < FRAME_BUDGET_MS) {
            registerOne(active.pack, active.files[active.completed]);
            active.completed++;
          }
          state().active = { group: active.group, total: active.total, completed: active.completed };
          updateStatus(active);
          if (active.completed < active.total) {
            requestAnimationFrame(frame);
            return;
          }
          finishCurrent(active);
        } catch (error) {
          failCurrent(active, error);
        }
      }
      requestAnimationFrame(frame);
    }

    lz4.loadPackage = function stagedLoadPackage(pack, preloadPlugin) {
      if (preloadPlugin || !pack || !pack.metadata || !Array.isArray(pack.metadata.files) || !pack.compressedData) {
        return originalLoadPackage(pack, preloadPlugin);
      }
      if (!Module.calledRun && !preRunDependencyHeld) {
        preRunDependencyHeld = true;
        Module.addRunDependency(PRE_RUN_DEPENDENCY);
      }
      const group = labelFor(pack);
      queue.push({ pack: pack, group: group });
      trace("enqueue", { group: group, files: pack.metadata.files.length, queued: queue.length });
      startNext();
    };

    window.CDDA_STAGED_LOAD_PACKAGE = function loadOneStagedPackage(group, loaderUrl) {
      return new Promise(function(resolve, reject) {
        function complete(event) {
          if (event.detail && event.detail.group === group) {
            window.removeEventListener("cdda-staged-package-complete", complete);
            window.removeEventListener("cdda-staged-package-failed", failed);
            resolve(event.detail);
          }
        }
        function failed(event) {
          if (event.detail && event.detail.group === group) {
            window.removeEventListener("cdda-staged-package-complete", complete);
            window.removeEventListener("cdda-staged-package-failed", failed);
            reject(new Error(event.detail.error));
          }
        }
        window.addEventListener("cdda-staged-package-complete", complete);
        window.addEventListener("cdda-staged-package-failed", failed);
        window.__cddaExpectedStagedPackage = group;
        const script = document.createElement("script");
        script.src = loaderUrl;
        script.async = true;
        script.onerror = function() {
          window.removeEventListener("cdda-staged-package-complete", complete);
          window.removeEventListener("cdda-staged-package-failed", failed);
          reject(new Error("Unable to load staged package loader: " + loaderUrl));
        };
        document.head.appendChild(script);
      });
    };

    // QA-only, read-only diagnostic.  It walks node metadata only; it neither
    // reads virtual files nor changes LZ4 caches, package buffers, or FS nodes.
    window.CDDA_STAGED_COLLECT_FS_INVENTORY = function collectFsInventory() {
      const seenBackings = new Set();
      const activeNodes = new Set();
      const result = {
        schema: "cdda-fs-inventory-v1",
        lz4VirtualFiles: 0,
        lz4VirtualBytes: 0,
        lz4CompressedBackingBuffers: 0,
        lz4CompressedBackingBytes: 0,
        lz4FixedCacheBytes: 0,
        memfsFiles: 0,
        memfsLogicalBytes: 0,
        memfsBackingBuffers: 0,
        memfsBackingBytes: 0,
        idbfsFiles: 0,
        idbfsLogicalBytes: 0,
        idbfsBackingBuffers: 0,
        idbfsBackingBytes: 0,
        directories: 0,
        otherNodes: 0,
        notes: [
          "Logical LZ4 virtual-file bytes are not resident decoded bytes.",
          "LZ4 backing bytes include each compressedData.data buffer once.",
          "This inventory does not measure browser-native PSS by itself."
        ]
      };

      function addUniqueBacking(kind, value) {
        if (!value || typeof value.byteLength !== "number") return;
        const backing = ArrayBuffer.isView(value) ? value.buffer : value;
        if (!backing || typeof backing.byteLength !== "number" || seenBackings.has(backing)) return;
        seenBackings.add(backing);
        result[kind + "BackingBuffers"]++;
        result[kind + "BackingBytes"] += backing.byteLength;
      }

      function mountedAtIdbfs(node) {
        for (let current = node; current && current.parent && current !== current.parent; current = current.parent) {
          if (current.mount && current.mount.type && current.mount.type.name === "IDBFS") return true;
        }
        return false;
      }

      function walk(node) {
        if (!node || activeNodes.has(node)) return;
        activeNodes.add(node);
        const contents = node.contents;
        if (contents && contents.compressedData) {
          result.lz4VirtualFiles++;
          result.lz4VirtualBytes += Number(node.size) || 0;
          addUniqueBacking("lz4Compressed", contents.compressedData.data);
          const cacheCount = Array.isArray(contents.compressedData.cachedChunks) ?
            contents.compressedData.cachedChunks.length : 0;
          result.lz4FixedCacheBytes += cacheCount * lz4.CHUNK_SIZE;
        } else if (contents && typeof contents.byteLength === "number") {
          const kind = mountedAtIdbfs(node) ? "idbfs" : "memfs";
          result[kind + "Files"]++;
          result[kind + "LogicalBytes"] += Number(node.size) || 0;
          addUniqueBacking(kind, contents);
        } else if (contents && typeof contents === "object") {
          result.directories++;
          Object.keys(contents).forEach(function(name) { walk(contents[name]); });
        } else {
          result.otherNodes++;
        }
      }

      walk(fs.root);
      result.observedAtMs = Math.round(now());
      trace("fs-inventory", result);
      return result;
    };
  });
})();
