SCHEDULER_STACK  := lmi-scheduler
UI_STACK         := lmi-scheduler-ui
STAGE            ?= prod
REGION           ?= ap-southeast-2
STRATEGY         ?= shape
SCALE_DOWN_CRON  ?= cron(0 22 ? * MON-FRI *)
SCALE_UP_CRON    ?= cron(15 6 ? * MON-FRI *)

AWS_OPTS := --region $(REGION)
DEPLOY_FLAGS := --no-confirm-changeset --no-fail-on-empty-changeset --resolve-s3 --region $(REGION)

# Resolve API endpoint once (lazy, cached per invocation)
API_ENDPOINT = $(shell aws cloudformation describe-stacks $(AWS_OPTS) \
	--stack-name $(SCHEDULER_STACK) \
	--query "Stacks[0].Outputs[?OutputKey=='ApiEndpoint'].OutputValue" \
	--output text 2>/dev/null)

.PHONY: deploy-scheduler teardown-scheduler outputs status \
        register deregister scale-down scale-up \
        cost-report list-registrations help \
        ui-install ui-dev ui-build ui-deploy \
        ui-stack-deploy ui-stack-teardown

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-25s\033[0m %s\n", $$1, $$2}'
	@echo ""
	@echo "  See examples/Makefile for demo infrastructure targets."

deploy-scheduler: ## Build and deploy the scheduler SAM stack
	@echo "==> Deploying scheduler stack ($(SCHEDULER_STACK))..."
	sam build && sam deploy \
		--parameter-overrides StageName=$(STAGE) \
		$(DEPLOY_FLAGS)

teardown-scheduler: ## Delete the scheduler stack
	@echo "==> Deleting scheduler stack ($(SCHEDULER_STACK))..."
	aws cloudformation delete-stack $(AWS_OPTS) --stack-name $(SCHEDULER_STACK)
	aws cloudformation wait stack-delete-complete $(AWS_OPTS) --stack-name $(SCHEDULER_STACK)

outputs: ## Print the scheduler API endpoint
	@echo ""
	@echo "=========================================="
	@echo "  Scheduler Outputs"
	@echo "=========================================="
	@echo ""
	@API=$$(aws cloudformation describe-stacks $(AWS_OPTS) \
		--stack-name $(SCHEDULER_STACK) \
		--query "Stacks[0].Outputs[?OutputKey=='ApiEndpoint'].OutputValue" \
		--output text 2>/dev/null || echo ""); \
	echo "  API Endpoint:  $${API:-NOT DEPLOYED}"; \
	echo ""; \
	echo "  Export for shell:"; \
	echo ""; \
	echo "    export API_ENDPOINT=$$API"; \
	echo ""

status: ## Show deployment status of the scheduler stack
	@echo ""
	@echo "  Stack Status"
	@echo "  ──────────────────────────────────────────────────"
	@printf "  %-35s %s\n" "$(SCHEDULER_STACK)" \
		"$$(aws cloudformation describe-stacks $(AWS_OPTS) --stack-name $(SCHEDULER_STACK) \
			--query 'Stacks[0].StackStatus' --output text 2>/dev/null || echo 'NOT DEPLOYED')"

list-registrations: ## List all registered capacity providers
	@curl -s "$(API_ENDPOINT)/registrations" | python3 -m json.tool

register: ## Register a CP (requires CP_NAME; optional STRATEGY, SCALE_DOWN_CRON, SCALE_UP_CRON)
ifndef CP_NAME
	$(error CP_NAME is required. Usage: make register CP_NAME=my-capacity-provider [STRATEGY=shape])
endif
	@echo "==> Registering $(CP_NAME) ..." && \
	echo "    Strategy:    $(STRATEGY)" && \
	echo "    Scale-down:  $(SCALE_DOWN_CRON)" && \
	echo "    Scale-up:    $(SCALE_UP_CRON)" && \
	echo "" && \
	curl -s -X POST "$(API_ENDPOINT)/registrations" \
		-H "Content-Type: application/json" \
		-d "{\"capacityProviderName\": \"$(CP_NAME)\", \"strategy\": \"$(STRATEGY)\", \"scaleDownSchedule\": \"$(SCALE_DOWN_CRON)\", \"scaleUpSchedule\": \"$(SCALE_UP_CRON)\"}" | python3 -m json.tool

deregister: ## Deregister a CP (requires CP_NAME)
ifndef CP_NAME
	$(error CP_NAME is required. Usage: make deregister CP_NAME=my-capacity-provider)
endif
	@echo "==> Deregistering $(CP_NAME) ..." && \
	code=$$(curl -s -o /tmp/lmi-deregister-body.txt -w "%{http_code}" -X DELETE "$(API_ENDPOINT)/registrations/$(CP_NAME)") && \
	if [ -s /tmp/lmi-deregister-body.txt ]; then cat /tmp/lmi-deregister-body.txt | python3 -m json.tool; else echo "Deregistered ($$code)."; fi

scale-down: ## Invoke scale-down for a CP (requires CP_NAME)
ifndef CP_NAME
	$(error CP_NAME is required. Usage: make scale-down CP_NAME=my-capacity-provider)
endif
	@echo "==> Invoking scale-down for $(CP_NAME) ..." && \
	echo '{"capacityProviderName":"$(CP_NAME)","action":"scale-down"}' > /tmp/lmi-executor-payload.json && \
	aws lambda invoke $(AWS_OPTS) --function-name lmi-scheduler-executor-$(STAGE) \
		--payload file:///tmp/lmi-executor-payload.json \
		--cli-binary-format raw-in-base64-out \
		/tmp/lmi-executor-response.json && \
	echo "Response:" && cat /tmp/lmi-executor-response.json | python3 -m json.tool

scale-up: ## Invoke scale-up for a CP (requires CP_NAME)
ifndef CP_NAME
	$(error CP_NAME is required. Usage: make scale-up CP_NAME=my-capacity-provider)
endif
	@echo "==> Invoking scale-up for $(CP_NAME) ..." && \
	echo '{"capacityProviderName":"$(CP_NAME)","action":"scale-up"}' > /tmp/lmi-executor-payload.json && \
	aws lambda invoke $(AWS_OPTS) --function-name lmi-scheduler-executor-$(STAGE) \
		--payload file:///tmp/lmi-executor-payload.json \
		--cli-binary-format raw-in-base64-out \
		/tmp/lmi-executor-response.json && \
	echo "Response:" && cat /tmp/lmi-executor-response.json | python3 -m json.tool

cost-report: ## Get cost report (optional CP_NAME for single CP; omit for aggregate)
	@API="$(API_ENDPOINT)" && \
	PARAMS="" && \
	if [ -n "$(START)" ] || [ -n "$(END)" ]; then \
		PARAMS="?"; \
		if [ -n "$(START)" ]; then PARAMS="$${PARAMS}start=$(START)"; fi; \
		if [ -n "$(START)" ] && [ -n "$(END)" ]; then PARAMS="$${PARAMS}&"; fi; \
		if [ -n "$(END)" ]; then PARAMS="$${PARAMS}end=$(END)"; fi; \
	fi && \
	if [ -n "$(CP_NAME)" ]; then \
		echo "==> Cost report for $(CP_NAME) ..." && \
		curl -s "$$API/reports/$(CP_NAME)$${PARAMS}" | python3 -m json.tool; \
	else \
		echo "==> Aggregate cost report ..." && \
		curl -s "$$API/reports$${PARAMS}" | python3 -m json.tool; \
	fi

# ---------------------------------------------------------------------------
#  UI targets
# ---------------------------------------------------------------------------

ui-install: ## Install UI dependencies
	npm install --prefix ui

ui-dev: ## Start UI dev server (requires API_ENDPOINT)
	NUXT_PUBLIC_API_BASE_URL=$(API_ENDPOINT) npm run dev --prefix ui

ui-build: ## Build UI for production (requires API_ENDPOINT)
	NUXT_PUBLIC_API_BASE_URL=$(API_ENDPOINT) npm run generate --prefix ui

ui-deploy: ## Deploy UI to S3 and invalidate CloudFront cache
	@BUCKET=$$(aws cloudformation describe-stacks $(AWS_OPTS) \
		--stack-name $(UI_STACK) \
		--query "Stacks[0].Outputs[?OutputKey=='S3BucketName'].OutputValue" \
		--output text) && \
	DIST_ID=$$(aws cloudformation describe-stacks $(AWS_OPTS) \
		--stack-name $(UI_STACK) \
		--query "Stacks[0].Outputs[?OutputKey=='CloudFrontDistributionId'].OutputValue" \
		--output text) && \
	echo "==> Syncing to s3://$$BUCKET ..." && \
	aws s3 sync ui/.output/public/ "s3://$$BUCKET" --delete $(AWS_OPTS) && \
	echo "==> Invalidating CloudFront distribution $$DIST_ID ..." && \
	aws cloudfront create-invalidation --distribution-id "$$DIST_ID" --paths "/*" $(AWS_OPTS)

# ---------------------------------------------------------------------------
#  UI hosting stack targets
# ---------------------------------------------------------------------------

ui-stack-deploy: ## Deploy the UI hosting stack (S3 + CloudFront)
	@echo "==> Deploying UI hosting stack ($(UI_STACK))..."
	sam deploy \
		--template-file ui/template.yaml \
		--stack-name $(UI_STACK) \
		$(DEPLOY_FLAGS)

ui-stack-teardown: ## Delete the UI hosting stack
	@echo "==> Deleting UI hosting stack ($(UI_STACK))..."
	aws cloudformation delete-stack $(AWS_OPTS) --stack-name $(UI_STACK)
	aws cloudformation wait stack-delete-complete $(AWS_OPTS) --stack-name $(UI_STACK)
