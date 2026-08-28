/*
 * Manifest-driven staged package runtime.  It preserves every package and
 * virtual path; it changes only when a complete package is registered.
 */
(function installCddaStagedPhaseRuntime() {
  "use strict";

  const bootGroups = [
    "boot-audio",
    "boot-core-metadata",
    "boot-fonts",
    "boot-gfx-catalog",
    "boot-gfx-common",
    "boot-localization",
    "boot-mod-metadata",
    // UI initialization reads palettes and keybindings before the menu.  The
    // complete existing package remains intact; only its registration is early.
    "world-core-support"
  ];
  let manifest = null;
  let serial = Promise.resolve();
  const pendingOrLoaded = new Map();

  function fail(message) {
    throw new Error("CDDA staged package runtime: " + message);
  }

  function requireManifest() {
    if (!manifest || !manifest.packages) fail("manifest was not initialized");
    return manifest;
  }

  function groupLoader(group) {
    const spec = requireManifest().packages[group];
    if (!spec || typeof spec.loader_file !== "string") {
      fail("unknown package group " + group);
    }
    return spec.loader_file;
  }

  function enqueueGroup(group) {
    if (pendingOrLoaded.has(group)) return pendingOrLoaded.get(group);
    const task = serial.then(function() {
      if (typeof window.CDDA_STAGED_LOAD_PACKAGE !== "function") {
        fail("LZ4 registration hook is not ready for " + group);
      }
      return window.CDDA_STAGED_LOAD_PACKAGE(group, groupLoader(group));
    });
    pendingOrLoaded.set(group, task);
    // Keep exactly one registration in flight globally.  Preserve an error for
    // the caller, but allow a separately requested group to report its own
    // loader failure instead of inheriting a rejected queue promise.
    serial = task.catch(function() { return undefined; });
    return task;
  }

  function ensureGroups(groups) {
    const unique = Array.from(new Set(groups));
    return Promise.all(unique.map(enqueueGroup));
  }

  function ensureMods(ids) {
    const maps = requireManifest().runtime_group_maps || {};
    const lookup = maps.mod_id_to_group || {};
    return ensureGroups(ids.map(function(id) {
      const group = lookup[id];
      if (!group) fail("no package mapping for active MOD " + id);
      return group;
    }));
  }

  function ensureTilesets(ids) {
    const maps = requireManifest().runtime_group_maps || {};
    const lookup = maps.tile_id_to_group || {};
    return ensureGroups(ids.map(function(id) {
      const group = lookup[id];
      if (!group) fail("no package mapping for selected tileset " + id);
      return group;
    }));
  }

  window.CDDA_STAGED_PHASES_ENABLED = true;
  window.CDDA_STAGED_BOOT_GROUPS = bootGroups.slice();
  window.CDDA_STAGED_ENSURE_GROUPS = ensureGroups;
  window.CDDA_STAGED_ENSURE_MODS = ensureMods;
  window.CDDA_STAGED_ENSURE_TILESETS = ensureTilesets;
  window.CDDA_STAGED_RUNTIME_READY = fetch("staged-package-manifest.json", { cache: "no-store" })
    .then(function(response) {
      if (!response.ok) fail("manifest request failed: HTTP " + response.status);
      return response.json();
    })
    .then(function(value) {
      manifest = value;
      const invariant = manifest.lossless_invariants || {};
      if (!invariant.package_path_count_matches_source ||
          !invariant.package_paths_unique ||
          !invariant.package_path_set_sha256_matches_source) {
        fail("manifest lossless invariants are false");
      }
      bootGroups.forEach(groupLoader);
      console.info("[CDDA stage] manifest ready", {
        packageCount: Object.keys(manifest.packages).length,
        pathCount: manifest.source_file_count,
        bootGroups: bootGroups
      });
      return manifest;
    });
})();
