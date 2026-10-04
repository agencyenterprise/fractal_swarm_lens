// Web plugins ship an ES module (manifest.ui.module) that exports `install(host, manifest)`.
// Modules load in parallel and install in manifest order, so menus are stable. A plugin that fails
// is reported by name and the others still install; the explorer never depends on any one plugin.
export async function installWebPlugins(manifests, { hostFor, report, load = (url) => import(url) }) {
  const withModules = manifests.filter((manifest) => manifest.ui?.module);
  const loaded = await Promise.allSettled(withModules.map((manifest) => load(manifest.ui.module)));
  const installed = [];
  for (const [index, manifest] of withModules.entries()) {
    try {
      await install(manifest, loaded[index], hostFor(manifest));
      installed.push(manifest.id);
    } catch (error) {
      report(new Error(`Plugin ${manifest.title || manifest.id} failed to load: ${error.message}`, { cause: error }));
    }
  }
  return installed;
}

async function install(manifest, outcome, host) {
  if (outcome.status === "rejected") throw outcome.reason;
  if (typeof outcome.value.install !== "function") throw new Error(`${manifest.ui.module} does not export install(host, manifest)`);
  await outcome.value.install(host, manifest);
}
