// Web plugins ship an ES module (manifest.ui.module) that exports `install(host, manifest)`.
// Modules load in parallel and install in manifest order, so menus are stable. A plugin that fails
// is reported by name, its registrations are removed, and the others still install.
export async function installWebPlugins(manifests, { hostFor, report, load = (url) => import(url) }) {
  const withModules = manifests.filter((manifest) => manifest.ui?.module);
  const loaded = await Promise.allSettled(withModules.map((manifest) => load(manifest.ui.module)));
  const installed = [];
  for (const [index, manifest] of withModules.entries()) {
    const host = hostFor(manifest);
    try {
      await install(manifest, loaded[index], host);
      installed.push(manifest.id);
    } catch (error) {
      host.dispose();
      const reason = error instanceof Error ? error.message : String(error);
      report(new Error(`Plugin ${manifest.title || manifest.id} failed to load: ${reason}`, { cause: error }));
    }
  }
  return installed;
}

async function install(manifest, outcome, host) {
  if (outcome.status === "rejected") throw outcome.reason;
  if (typeof outcome.value.install !== "function") throw new Error(`${manifest.ui.module} does not export install(host, manifest)`);
  await outcome.value.install(host, manifest);
}
