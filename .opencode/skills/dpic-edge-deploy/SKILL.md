---
name: dpic-edge-deploy
description: >
  Deploys a DPIC web app to AWS behind one shared login checked at the CloudFront edge, with
  images in ECR and no registry password or long-lived CI key anywhere. The pattern used by
  ai-for-panchayats (ECS behind a load balancer) and janasunani (one EC2 box running Docker
  Compose behind Caddy). Use this skill when the user asks to deploy, host, publish, or
  password-protect a DPIC app or dashboard on AWS, to put CloudFront or basic auth in front of
  a service, to move images from GHCR to ECR, or to set up a GitHub Actions deploy with OIDC.
---

# DPIC edge deploy

A small known group (a DPIC team plus named officials) reaches the app through CloudFront. A
CloudFront Function checks one shared username and password before the cache and before the
origin, so a request without the login never reaches the app. The origin accepts only our
distribution. Images live in ECR; CI pushes through an OIDC role and the runtime pulls with its
own AWS role.

Two working references:

| Shape | Repo | Origin | Pulls with |
|---|---|---|---|
| ECS service behind an ALB | `ai-for-panchayats`, `infra/terraform/app/` | ALB, plain HTTP from CloudFront (no domain, so no origin certificate) | ECS task execution role |
| One EC2 box, Docker Compose, Caddy | `janasunani`, `deploy/terraform/`, `deploy/proxy/Caddyfile`, `deploy/deploy.sh` | Caddy on the box's `nip.io` name, **HTTPS** from CloudFront | EC2 instance role |

`templates/` holds the EC2 + Caddy shape with the project-specific values replaced by
`locals`. For the ALB shape, copy from `ai-for-panchayats` instead.

## Before you start: is a shared login enough?

One credential is not identity. It cannot say who looked at what, and it cannot be taken from
one person without rotating it for everyone. It fits a bounded group. Write down who the group
is and why each of them may see what the app serves (for janasunani: the DPIC team and the
Director Grievance, GAPG, all authorised to see raw complaints). If the audience is wider, or
the data needs per-person audit, plan per-person sign-in (Cognito or OIDC on the load
balancer) instead.

## Workflow

1. **Read the project's current infrastructure first.** Find any existing Terraform state and
   what it manages. If a stateful resource (a database box, a volume) is in that state, every
   plan must be read for `must be replaced` or `to destroy` before any apply. Add
   `lifecycle { prevent_destroy = true }` to it if it lacks one.
2. **Copy the templates** into the project's Terraform directory and set the `locals`
   (project name, GitHub repo, origin domain, AWS account). Add `hashicorp/random` to
   `required_providers`.
3. **Lock the origin:**
   - EC2: `templates/origin.tf`. Port 443 from the `com.amazonaws.global.cloudfront.origin-facing`
     prefix list only; port 80 stays open to the world for Let's Encrypt's HTTP-01 renewal.
   - Caddy: use `templates/Caddyfile`. It returns 403 without `X-Origin-Verify`, strips that
     header before proxying, and keeps a health path open for the on-box smoke check. Put
     `terraform output -raw origin_verify_secret` into `deploy/proxy.env` as
     `ORIGIN_VERIFY_SECRET` (chmod 600).
   - ALB: a listener rule that forwards only when `X-Origin-Verify` matches (see
     panchayats `service.tf`).
4. **Registry:** `templates/ecr.tf`. Immutable app repositories, a separate mutable cache
   repository, a push role trusted only from `refs/heads/main`, pull-only access for the
   runtime role.
5. **GitHub:**
   - An environment (e.g. `box-deploy` or `production`) with **you as a required reviewer**
     and deployment branches limited to `main`.
   - The deploy job declares that environment. Its OIDC role's trust pins
     `repo:<org>/<repo>:environment:<name>`. Put the box SSH key, known hosts and role ARN on
     the environment, not the repo.
   - Build jobs don't declare the environment; their push role trusts
     `repo:<org>/<repo>:ref:refs/heads/main`, and its ARN is a repo-level variable.
   - Build jobs skip a commit already in ECR (immutable tags). The deploy job refuses a tag
     missing from ECR **before** it opens SSH or changes anything.
6. **The runtime never migrates a production database on start.** It checks the schema
   revision and refuses when a migration is pending; migrations are a manual step after a
   backup. `deploy.sh` runs the same check before swapping containers.
7. **Fail closed:** the deploy script refuses to start without a real origin secret (32+
   letters and digits); compose never uses `:?` for it (that breaks a database-only `up`).
8. **Plan, read, apply.** `terraform plan -out`, read every change, then apply that saved
   plan. The person applying runs it themselves if the agent is not permitted to.
9. **Verify** (below), then write the runbook section: where the password comes from, who
   holds it, how to rotate it, how to roll back.

## Traps that have already cost time

- **Host header with an HTTPS origin.** CloudFront checks the origin's certificate against
  the Host it forwards. `Managed-AllViewer` forwards the viewer's `*.cloudfront.net` Host and
  every request fails with a 502. Use `Managed-AllViewerExceptHostHeader`. (Panchayats uses
  AllViewer because its origin is plain HTTP.)
- **Caddy directive order.** In the default order `handle` runs before `respond`, so a
  top-level `respond @not_from_cloudfront 403` never fires. Put it first inside a `route`
  block. Test it against the pinned Caddy image, not by reading.
- **Security group rule quota.** The CloudFront prefix list counts as 55 of a group's default
  60 rules. SSH (one CIDR), HTTP and one temporary CI SSH rule fit; stale SSH CIDRs added by
  hand do not. The apply removes hand-added rules, so set `admin_cidr` to your current
  address first or you lose SSH.
- **ECR and BuildKit cache.** Immutable repositories refuse the cache's moving tag, so the
  cache gets its own mutable repository, and ECR needs
  `image-manifest=true,oci-mediatypes=true` on `cache-to`.
- **Lifecycle rules.** Expire untagged images only. An age rule on tagged images eventually
  deletes the one that is running, and the next restart cannot pull.
- **Strip the credential at the edge.** The function deletes `Authorization` before
  forwarding; the origin has no use for it.
- **Origin timeout.** CloudFront waits 60 s for the origin by default (quota up to 180 s). A
  synchronous request that runs models or OCR can pass that and show the user a 504 while the
  origin finishes. Measure it; make long work asynchronous.
- **The credential is readable.** It is compiled into the CloudFront Function body, so anyone
  with `cloudfront:GetFunction` in the account can read it, and it is in the Terraform state.
- **`terraform output a b` fails.** It takes one name at a time.

## Verify

```bash
URL=$(terraform output -raw site_url)
U=$(terraform output -raw basic_auth_username)
PW=$(terraform output -raw basic_auth_password)
curl -s -o /dev/null -w "%{http_code}\n" "$URL/"                     # 401
curl -s -o /dev/null -w "%{http_code}\n" -u "$U:$PW" "$URL/"         # 200
ASSET=$(curl -s -u "$U:$PW" "$URL/" | grep -o '/_next/static/[^"]*' | head -1)  # a real hashed file
curl -s -o /dev/null -w "%{http_code}\n" -u "$U:$PW" "$URL$ASSET"   # 200, and 401 without -u
curl -s -o /dev/null -w "%{http_code}\n" --max-time 10 "https://<origin-domain>/" # timeout or 403
```

For Caddy, before merging, run the pinned image with the Caddyfile and throwaway upstreams, and
check: no header → 403; wrong or extended header → 403; health path → 200;
`curl --path-as-is /api/health/../other` → 403; right header → 200 on each route, with no
`X-Origin-Verify` reaching the upstream; an empty `ORIGIN_VERIFY_SECRET` → Caddy refuses to start.

## Templates

- `templates/cdn.tf`: distribution, origin secret, cache behaviours, both gated.
- `templates/auth.tf` and `templates/basic_auth.js.tftpl`: the edge login.
- `templates/ecr.tf`: repositories, lifecycle, push role, deploy and runtime permissions.
- `templates/origin.tf`: the box's 443 rule, CloudFront only (EC2 shape).
- `templates/outputs.tf`: site URL, login and origin secret, as the steps read them.
- `templates/Caddyfile`: origin check for the EC2 shape.
