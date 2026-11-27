# Mental Loader Railway infrastructure

Use the repository's [deployment guide](../docs/deployment.md). Start by setting the personal
workspace's **US$10 Compute hard limit** and US$8 alert. This file cannot set the billing cap.
The user accepts stopping the services when the cap is reached.

`railway.ts` uses Railway's supported TypeScript IaC API with the pinned SDK in package-lock.
It defines PostgreSQL 17, Web, Hermes and persistent app/native state volumes, with sleeping
disabled and one replica per service. Both services build the same GitHub repository/main branch with separate Dockerfiles.
Connect Railway to the personal GitHub repository and apply IaC once; pushing source alone
does not apply infrastructure. Secrets/readiness flags come from
Railway environment Shared Variables, imported privately from `.local/railway-inputs/shared.env`.

Run `npm ci --prefix .railway --ignore-scripts` and `npm --prefix .railway run check` for offline
validation. `railway config plan` previews the live environment. `railway config apply` creates
paid resources after review. Neither was run by preparing this configuration. Do not apply
the complete project definition to an unrelated environment.
