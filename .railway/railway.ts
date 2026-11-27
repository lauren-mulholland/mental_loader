import { database, defineRailway, github, project, service, volume } from "railway/iac";

// Billing is NOT controlled by this file. Set a US$10 Compute hard limit in the
// Railway workspace before applying. The user accepts shutdown when it is reached.
export default defineRailway((ctx) => {
  const region = "us-west2";
  const webData = volume("web-state", { region, sizeMB: 1024 });
  const hermesData = volume("hermes-state", { region, sizeMB: 1024 });
  const postgres = database("Postgres", "postgres", {
    image: "ghcr.io/railwayapp-templates/postgres-ssl:17",
    output: "DATABASE_URL",
    defaultMountPath: "/var/lib/postgresql/data",
    region,
  });
  postgres.deploy = {
    ...postgres.deploy,
    numReplicas: 1,
    sleepApplication: false,
    limitOverride: { containers: { cpu: 0.5, memoryBytes: 256 * 1024 * 1024 } },
  };

  // Both services build main from the same repository, using distinct Dockerfiles.
  // Repository access and initial IaC apply still need to be completed in Railway.
  const hermes = service("Hermes", {
    source: github("lauren-mulholland/mental_loader", { branch: "main" }),
    build: { builder: "DOCKERFILE", dockerfilePath: "deploy/Dockerfile.hermes" },
    replicas: { [region]: 1 },
    volumeMounts: { "/data": hermesData },
    deploy: {
      sleepApplication: false,
      restartPolicyType: "ON_FAILURE",
      restartPolicyMaxRetries: 3,
      requiredMountPath: "/data",
      overlapSeconds: 0,
      drainingSeconds: 30,
      limitOverride: { containers: { cpu: 0.5, memoryBytes: 512 * 1024 * 1024 } },
    },
    env: {
      PORT: "8642",
      DEPLOYMENT_READY: ctx.shared.HERMES_READY,
      API_SERVER_KEY: ctx.shared.HERMES_API_KEY,
      ML_HOUSEHOLD_ACCESS_CODE: ctx.shared.HERMES_TOOL_TOKEN,
      ML_BACKEND_URL: ctx.shared.WEB_ORIGIN,
      OPENROUTER_API_KEY: ctx.shared.OPENROUTER_API_KEY,
      HERMES_HOME: "/data/hermes-home",
      HERMES_RUNTIME_DIR: "/data/hermes-managed",
      ML_HOSTED_WORKDIR: "/data/workspace",
    },
  });

  const web = service("Web", {
    source: github("lauren-mulholland/mental_loader", { branch: "main" }),
    build: { builder: "DOCKERFILE", dockerfilePath: "deploy/Dockerfile.web" },
    replicas: { [region]: 1 },
    healthcheck: "/healthz",
    healthcheckTimeout: 120,
    volumeMounts: { "/data": webData },
    deploy: {
      sleepApplication: false,
      restartPolicyType: "ON_FAILURE",
      restartPolicyMaxRetries: 3,
      requiredMountPath: "/data",
      overlapSeconds: 0,
      drainingSeconds: 30,
      limitOverride: { containers: { cpu: 0.5, memoryBytes: 512 * 1024 * 1024 } },
    },
    env: {
      PORT: "10000",
      DEPLOYMENT_READY: ctx.shared.WEB_READY,
      PUBLIC_ORIGIN: ctx.shared.WEB_ORIGIN,
      DATABASE_URL: postgres.env.DATABASE_URL,
      // A separate host variable avoids assuming a public Hermes endpoint.
      HERMES_HOST: hermes.env.RAILWAY_PRIVATE_DOMAIN,
      HERMES_PORT: "8642",
      HERMES_API_KEY: ctx.shared.HERMES_API_KEY,
      HERMES_TOOL_TOKEN: ctx.shared.HERMES_TOOL_TOKEN,
      HERMES_HOUSEHOLD_ID: ctx.shared.HERMES_HOUSEHOLD_ID,
      APP_SECRET_KEY_B64: ctx.shared.APP_SECRET_KEY_B64,
      SECRET_KEY_PATH: "/data/app-secret.key",
      VAPID_PRIVATE_KEY_B64: ctx.shared.VAPID_PRIVATE_KEY_B64,
      VAPID_PUBLIC_KEY: ctx.shared.VAPID_PUBLIC_KEY,
      VAPID_SUBJECT: ctx.shared.VAPID_SUBJECT,
      HOSTED_MODE: "true",
      COOKIE_SECURE: "true",
      ALLOW_LEGACY_LOGIN: "false",
      PAAS_PERSONAL_RUNTIME: "true",
      AI_ENABLED: "true",
      AI_DAILY_CALL_LIMIT: "20",
    },
  });

  return project("mental-loader", {
    resources: [postgres, webData, hermesData, hermes, web],
  });
});
