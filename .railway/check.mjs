// Offline safety checks: this never connects to Railway or reads private input files.
import assert from 'node:assert/strict';
import { createRailwayContext, project, validateGraph } from 'railway/iac';
import configuration from './railway.ts';

const definition = await configuration(createRailwayContext({ environment: 'production' }), project);
const resources = definition.resources.flat();
const errors = validateGraph({
  version: 1, project: { name: definition.name },
  environments: [{ name: 'production' }], resources, edges: [],
});
assert.deepEqual(errors, []);
const byName = Object.fromEntries(resources.map(resource => [resource.name, resource]));
assert.equal(byName.Postgres.engine, 'postgres');
assert.match(byName.Postgres.image, /:17$/);
assert.equal(byName.Web.variables.DATABASE_URL.resource, 'database.Postgres');
assert.equal(byName.Web.variables.HERMES_HOST.resource, 'service.Hermes');
for (const name of ['Web', 'Hermes', 'Postgres']) {
  const service = byName[name];
  assert.equal(service.deploy.sleepApplication, false, `${name} must remain awake`);
  assert.equal(service.deploy.limitOverride.containers.cpu, 0.5);
  assert(!service.networking?.tcpProxies, `${name} must not expose a public TCP proxy`);
}
for (const [name, readiness] of [['Web', 'WEB_READY'], ['Hermes', 'HERMES_READY']]) {
  const service = byName[name];
  assert.equal(service.variables.DEPLOYMENT_READY.type, 'sharedReference');
  assert.equal(service.variables.DEPLOYMENT_READY.name, readiness);
  assert.equal(service.deploy.limitOverride.containers.memoryBytes, 512 * 1024 * 1024);
  assert.equal(Object.values(service.volumeAttachments)[0].mountPath, '/data');
  assert.equal(service.source.type, 'github');
  assert.equal(service.source.repo, 'lauren-mulholland/mental_loader');
  assert.equal(service.source.branch, 'main');
}
assert(!byName.Hermes.networking?.serviceDomains, 'Hermes must remain private');
console.log('PASS: Railway graph, persistent storage, private routing, awake services and resource limits.');
console.log('Workspace US$10 hard cap must be configured separately before apply.');
