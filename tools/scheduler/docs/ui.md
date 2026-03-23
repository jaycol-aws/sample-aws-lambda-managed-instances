# Scheduler UI

A web dashboard for managing LMI scheduler schedules, triggering manual scale operations, and viewing cost reports. Built as a static SPA with Nuxt v4 (Vue 3), hosted on S3 + CloudFront as a separate stack from the backend.

> **Prerequisites**: Node.js 18+ and npm. The scheduler backend should be deployed (`make deploy-scheduler`) before building for production.

## Overview

The UI provides:

- **Dashboard** — all capacity providers at a glance, with schedule status, strategy, cron expressions, and estimated savings per CP
- **Schedule management** — add, edit, and remove schedules for capacity providers
- **Manual triggers** — scale down or scale up a CP on demand from the dashboard
- **Cost reports** — aggregate and per-CP savings charts with date range filtering

## Tech stack

| Component | Choice |
|-----------|--------|
| Framework | Nuxt v4 (Vue 3), static generation via `nuxt generate` |
| Styling | Tailwind CSS v4 (`@tailwindcss/vite` plugin) |
| Validation | Zod (API responses + form inputs) |
| Charts | Chart.js via vue-chartjs |
| Hosting | S3 (private) + CloudFront with OAC |

## Local development

### Install dependencies

```bash
make ui-install
```

### Start the dev server

Point the UI at your deployed API endpoint:

```bash
NUXT_PUBLIC_API_BASE_URL=https://abc123.execute-api.ap-southeast-2.amazonaws.com/prod make ui-dev
```

The dev server starts on `http://localhost:3000`. Hot module replacement is enabled — changes to `.vue` files reflect immediately.

If you don't have the backend deployed yet, you can still run the dev server to work on layout and styling. API calls will fail, but loading, error, and empty states are all visible:

```bash
NUXT_PUBLIC_API_BASE_URL=http://localhost:9999 make ui-dev
```

### Type checking

```bash
cd ui && npm run typecheck
```

## Production build

The UI is built as a fully static SPA. The API endpoint is baked in at build time via the `NUXT_PUBLIC_API_BASE_URL` environment variable.

```bash
make ui-build
```

This resolves the API endpoint from the deployed scheduler stack's CloudFormation outputs automatically. The static output is written to `ui/.output/public/`.

To build against a specific endpoint:

```bash
NUXT_PUBLIC_API_BASE_URL=https://your-api.example.com/prod make ui-build
```

## Deployment

The UI hosting infrastructure is a **separate stack** from the scheduler backend. Each has its own lifecycle — you can redeploy the UI without touching the backend and vice versa.

### 1. Deploy the hosting stack

```bash
make ui-stack-deploy
```

This creates the `lmi-scheduler-ui` CloudFormation stack containing:

| Resource | Description |
|----------|-------------|
| S3 bucket | Private, all public access blocked. Stores the static SPA files |
| CloudFront distribution | HTTPS, OAC for S3 access, SPA routing (403/404 → `/index.html`) |
| Origin Access Control | Grants CloudFront read access to the S3 bucket |
| S3 bucket policy | Allows `s3:GetObject` only from the CloudFront distribution |

### 2. Build the UI

```bash
make ui-build
```

### 3. Deploy the UI files

```bash
make ui-deploy
```

This syncs `ui/.output/public/` to the S3 bucket and creates a CloudFront cache invalidation.

### 4. Access the UI

The CloudFront domain is in the stack outputs:

```bash
aws cloudformation describe-stacks \
  --stack-name lmi-scheduler-ui \
  --query "Stacks[0].Outputs[?OutputKey=='CloudFrontDomainName'].OutputValue" \
  --output text
```

### Teardown

```bash
make ui-stack-teardown
```

> **Note**: The S3 bucket must be empty before the stack can be deleted. Run `aws s3 rm s3://BUCKET_NAME --recursive` first if needed.

## Make targets

| Target | Description |
|--------|------------|
| `ui-install` | Install UI dependencies (`npm install`) |
| `ui-dev` | Start the Nuxt dev server (requires `API_ENDPOINT` or `NUXT_PUBLIC_API_BASE_URL`) |
| `ui-build` | Build static SPA via `nuxt generate` (requires `API_ENDPOINT`) |
| `ui-deploy` | Sync built files to S3 and invalidate CloudFront cache |
| `ui-stack-deploy` | Deploy the UI hosting stack (S3 + CloudFront) |
| `ui-stack-teardown` | Delete the UI hosting stack |

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `NUXT_PUBLIC_API_BASE_URL` | _(resolved from backend stack)_ | API Gateway endpoint URL, baked in at build time |
| `UI_STACK` | `lmi-scheduler-ui` | CloudFormation stack name for the UI hosting infrastructure |
| `REGION` | `ap-southeast-2` | AWS region for stack operations |

## Project structure

```text
ui/
├── nuxt.config.ts              # Nuxt config (SPA mode, Tailwind, runtime config)
├── app.vue                     # Root layout with navigation
├── template.yaml               # SAM template for S3 + CloudFront hosting
├── tailwind.css                # Tailwind v4 entry point
├── package.json
├── pages/
│   ├── index.vue               # Dashboard
│   ├── schedules/
│   │   ├── add.vue             # Add schedule form
│   │   └── [cpName]/edit.vue   # Edit schedule form
│   └── reports/
│       ├── index.vue           # Aggregate cost reports with charts
│       └── [cpName].vue        # Per-CP report detail
├── components/
│   ├── CpCard.vue              # CP card on dashboard
│   ├── ScheduleForm.vue        # Shared add/edit schedule form
│   ├── StatusBadge.vue         # Active / Scaled Down indicator
│   ├── ConfirmDialog.vue       # Confirmation modal
│   ├── SavingsChart.vue        # Time-series savings chart
│   ├── CpBreakdownTable.vue    # Per-CP savings table
│   └── DateRangePicker.vue     # Date range selector with presets
├── composables/
│   ├── useApi.ts               # API client (base URL + Zod validation)
│   ├── useCapacityProviders.ts # Fetch and merge CPs with schedules
│   └── useReports.ts           # Fetch and transform cost report data
├── schemas/                    # Zod schemas for API responses and forms
└── types/                      # TypeScript types inferred from schemas
```
