NETWORK_STACK    := lmi-example-network
CP_STACK         := lmi-example-capacity-provider
APP_STACK        := lmi-example-application
CP_SHAPE_STACK := lmi-example-capacity-provider-shape
APP_SHAPE_STACK := lmi-example-application-shape
SUBNET_COUNT     ?= 3
REGION           ?= ap-southeast-2

AWS_OPTS := --region $(REGION)
DEPLOY_FLAGS := --no-confirm-changeset --no-fail-on-empty-changeset --resolve-s3 --region $(REGION)

.PHONY: deploy-pause deploy-network deploy-pause-cp deploy-pause-app \
        deploy-shape deploy-shape-cp deploy-shape-app \
        teardown-pause teardown-pause-app teardown-pause-cp teardown-network \
        teardown-shape teardown-shape-app teardown-shape-cp \
        outputs status \
        register-pause-strategy deregister-pause-strategy scale-down-pause-strategy scale-up-pause-strategy cost-report-pause-strategy \
        register-shape-strategy deregister-shape-strategy scale-down-shape-strategy scale-up-shape-strategy cost-report-shape-strategy \
        help

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-30s\033[0m %s\n", $$1, $$2}'

deploy-pause: deploy-network deploy-pause-cp deploy-pause-app ## Deploy all pause stacks in order (network, CP, app)

deploy-network: ## Deploy the network stack (VPC, subnets, security group)
	@echo "==> Deploying network stack ($(NETWORK_STACK))..."
	cd network-stack && sam build && sam deploy \
		--stack-name $(NETWORK_STACK) \
		--capabilities CAPABILITY_IAM \
		--parameter-overrides SubnetCount=$(SUBNET_COUNT) \
		$(DEPLOY_FLAGS)

deploy-pause-cp: ## Deploy the pause capacity provider stack (requires network stack)
	@SUBNET_IDS=$$(aws cloudformation describe-stacks $(AWS_OPTS) \
		--stack-name $(NETWORK_STACK) \
		--query "Stacks[0].Outputs[?OutputKey=='SubnetIds'].OutputValue" \
		--output text) && \
	SG_ID=$$(aws cloudformation describe-stacks $(AWS_OPTS) \
		--stack-name $(NETWORK_STACK) \
		--query "Stacks[0].Outputs[?OutputKey=='SecurityGroupId'].OutputValue" \
		--output text) && \
	SUBNET1=$$(echo "$$SUBNET_IDS" | cut -d',' -f1) && \
	SUBNET2=$$(echo "$$SUBNET_IDS" | cut -d',' -f2 -s) && \
	SUBNET3=$$(echo "$$SUBNET_IDS" | cut -d',' -f3 -s) && \
	OVERRIDES="Subnet1=$$SUBNET1 SecurityGroupId=$$SG_ID" && \
	[ -n "$$SUBNET2" ] && OVERRIDES="$$OVERRIDES Subnet2=$$SUBNET2" || true && \
	[ -n "$$SUBNET3" ] && OVERRIDES="$$OVERRIDES Subnet3=$$SUBNET3" || true && \
	echo "==> Deploying capacity provider stack ($(CP_STACK))..." && \
	echo "    Subnets: $$SUBNET_IDS" && \
	echo "    Security Group: $$SG_ID" && \
	cd capacity-provider-stack && sam build && sam deploy \
		--stack-name $(CP_STACK) \
		--capabilities CAPABILITY_NAMED_IAM \
		--parameter-overrides $$OVERRIDES \
		$(DEPLOY_FLAGS)

deploy-pause-app: ## Deploy the pause application stack (requires pause CP stack)
	$(eval CP_ARN := $(shell aws cloudformation describe-stacks $(AWS_OPTS) \
		--stack-name $(CP_STACK) \
		--query "Stacks[0].Outputs[?OutputKey=='CapacityProviderArn'].OutputValue" \
		--output text))
	@echo "==> Deploying application stack ($(APP_STACK))..."
	@echo "    Capacity Provider ARN: $(CP_ARN)"
	cd application-stack && sam build && sam deploy \
		--stack-name $(APP_STACK) \
		--capabilities CAPABILITY_IAM \
		--parameter-overrides CapacityProviderArn=$(CP_ARN) \
		$(DEPLOY_FLAGS)

deploy-shape: deploy-shape-cp deploy-shape-app ## Deploy all shape stacks in order (CP, app)

deploy-shape-cp: ## Deploy the shape capacity provider stack (requires network stack)
	@SUBNET_IDS=$$(aws cloudformation describe-stacks $(AWS_OPTS) \
		--stack-name $(NETWORK_STACK) \
		--query "Stacks[0].Outputs[?OutputKey=='SubnetIds'].OutputValue" \
		--output text) && \
	SG_ID=$$(aws cloudformation describe-stacks $(AWS_OPTS) \
		--stack-name $(NETWORK_STACK) \
		--query "Stacks[0].Outputs[?OutputKey=='SecurityGroupId'].OutputValue" \
		--output text) && \
	SUBNET1=$$(echo "$$SUBNET_IDS" | cut -d',' -f1) && \
	SUBNET2=$$(echo "$$SUBNET_IDS" | cut -d',' -f2 -s) && \
	SUBNET3=$$(echo "$$SUBNET_IDS" | cut -d',' -f3 -s) && \
	OVERRIDES="Subnet1=$$SUBNET1 SecurityGroupId=$$SG_ID CapacityProviderName=lmi-example-cp-shape" && \
	[ -n "$$SUBNET2" ] && OVERRIDES="$$OVERRIDES Subnet2=$$SUBNET2" || true && \
	[ -n "$$SUBNET3" ] && OVERRIDES="$$OVERRIDES Subnet3=$$SUBNET3" || true && \
	echo "==> Deploying shape capacity provider stack ($(CP_SHAPE_STACK))..." && \
	echo "    Subnets: $$SUBNET_IDS" && \
	echo "    Security Group: $$SG_ID" && \
	cd capacity-provider-stack && sam build && sam deploy \
		--stack-name $(CP_SHAPE_STACK) \
		--capabilities CAPABILITY_NAMED_IAM \
		--parameter-overrides $$OVERRIDES \
		$(DEPLOY_FLAGS)

deploy-shape-app: ## Deploy the shape application stack (requires shape CP stack)
	$(eval CP_SHAPE_ARN := $(shell aws cloudformation describe-stacks $(AWS_OPTS) \
		--stack-name $(CP_SHAPE_STACK) \
		--query "Stacks[0].Outputs[?OutputKey=='CapacityProviderArn'].OutputValue" \
		--output text))
	@echo "==> Deploying shape application stack ($(APP_SHAPE_STACK))..."
	@echo "    Capacity Provider ARN: $(CP_SHAPE_ARN)"
	cd application-stack && sam build && sam deploy \
		--stack-name $(APP_SHAPE_STACK) \
		--capabilities CAPABILITY_IAM \
		--parameter-overrides CapacityProviderArn=$(CP_SHAPE_ARN) FunctionName=lmi-example-function-shape \
		$(DEPLOY_FLAGS)

teardown-pause: teardown-pause-app teardown-pause-cp teardown-network ## Tear down all pause stacks in reverse order

teardown-pause-app: ## Delete the pause application stack
	@echo "==> Deleting application stack ($(APP_STACK))..."
	aws cloudformation delete-stack $(AWS_OPTS) --stack-name $(APP_STACK)
	aws cloudformation wait stack-delete-complete $(AWS_OPTS) --stack-name $(APP_STACK)

teardown-pause-cp: ## Delete the pause capacity provider stack
	@echo "==> Deleting capacity provider stack ($(CP_STACK))..."
	aws cloudformation delete-stack $(AWS_OPTS) --stack-name $(CP_STACK)
	aws cloudformation wait stack-delete-complete $(AWS_OPTS) --stack-name $(CP_STACK)

teardown-network: ## Delete the network stack
	@echo "==> Deleting network stack ($(NETWORK_STACK))..."
	aws cloudformation delete-stack $(AWS_OPTS) --stack-name $(NETWORK_STACK)
	aws cloudformation wait stack-delete-complete $(AWS_OPTS) --stack-name $(NETWORK_STACK)

teardown-shape: teardown-shape-app teardown-shape-cp ## Tear down all shape stacks in reverse order

teardown-shape-app: ## Delete the shape application stack
	@echo "==> Deleting shape application stack ($(APP_SHAPE_STACK))..."
	aws cloudformation delete-stack $(AWS_OPTS) --stack-name $(APP_SHAPE_STACK)
	aws cloudformation wait stack-delete-complete $(AWS_OPTS) --stack-name $(APP_SHAPE_STACK)

teardown-shape-cp: ## Delete the shape capacity provider stack
	@echo "==> Deleting shape capacity provider stack ($(CP_SHAPE_STACK))..."
	aws cloudformation delete-stack $(AWS_OPTS) --stack-name $(CP_SHAPE_STACK)
	aws cloudformation wait stack-delete-complete $(AWS_OPTS) --stack-name $(CP_SHAPE_STACK)

outputs: ## Print example stack outputs (CP names, ARNs, function ARNs)
	@echo ""
	@echo "=========================================="
	@echo "  Example Stack Outputs"
	@echo "=========================================="
	@echo ""
	@CP_NAME=$$(aws cloudformation describe-stacks $(AWS_OPTS) \
		--stack-name $(CP_STACK) \
		--query "Stacks[0].Outputs[?OutputKey=='CapacityProviderName'].OutputValue" \
		--output text 2>/dev/null || echo ""); \
	CP_ARN=$$(aws cloudformation describe-stacks $(AWS_OPTS) \
		--stack-name $(CP_STACK) \
		--query "Stacks[0].Outputs[?OutputKey=='CapacityProviderArn'].OutputValue" \
		--output text 2>/dev/null || echo ""); \
	FN_ARN=$$(aws cloudformation describe-stacks $(AWS_OPTS) \
		--stack-name $(APP_STACK) \
		--query "Stacks[0].Outputs[?OutputKey=='FunctionArn'].OutputValue" \
		--output text 2>/dev/null || echo ""); \
	CP_SHAPE=$$(aws cloudformation describe-stacks $(AWS_OPTS) \
		--stack-name $(CP_SHAPE_STACK) \
		--query "Stacks[0].Outputs[?OutputKey=='CapacityProviderName'].OutputValue" \
		--output text 2>/dev/null || echo ""); \
	CP_SHAPE_ARN=$$(aws cloudformation describe-stacks $(AWS_OPTS) \
		--stack-name $(CP_SHAPE_STACK) \
		--query "Stacks[0].Outputs[?OutputKey=='CapacityProviderArn'].OutputValue" \
		--output text 2>/dev/null || echo ""); \
	FN_SHAPE_ARN=$$(aws cloudformation describe-stacks $(AWS_OPTS) \
		--stack-name $(APP_SHAPE_STACK) \
		--query "Stacks[0].Outputs[?OutputKey=='FunctionArn'].OutputValue" \
		--output text 2>/dev/null || echo ""); \
	echo "  Pause Example:"; \
	echo "    CP Name:       $${CP_NAME:-NOT DEPLOYED}"; \
	echo "    CP ARN:        $${CP_ARN:-NOT DEPLOYED}"; \
	echo "    Function ARN:  $${FN_ARN:-NOT DEPLOYED}"; \
	echo ""; \
	echo "  Shape Example:"; \
	echo "    CP Name:       $${CP_SHAPE:-NOT DEPLOYED}"; \
	echo "    CP ARN:        $${CP_SHAPE_ARN:-NOT DEPLOYED}"; \
	echo "    Function ARN:  $${FN_SHAPE_ARN:-NOT DEPLOYED}"; \
	echo ""

register-pause-strategy: ## Register the pause example CP (30-min scale cycle)
	@CP_NAME=$$(aws cloudformation describe-stacks $(AWS_OPTS) \
		--stack-name $(CP_STACK) \
		--query "Stacks[0].Outputs[?OutputKey=='CapacityProviderName'].OutputValue" \
		--output text) && \
	$(MAKE) -C .. register CP_NAME=$$CP_NAME STRATEGY=pause \
		SCALE_DOWN_CRON="cron(0/30 * * * ? *)" \
		SCALE_UP_CRON="cron(15/30 * * * ? *)"

deregister-pause-strategy: ## Deregister the pause example CP
	@CP_NAME=$$(aws cloudformation describe-stacks $(AWS_OPTS) \
		--stack-name $(CP_STACK) \
		--query "Stacks[0].Outputs[?OutputKey=='CapacityProviderName'].OutputValue" \
		--output text) && \
	$(MAKE) -C .. deregister CP_NAME=$$CP_NAME

scale-down-pause-strategy: ## Scale down the pause example CP
	@CP_NAME=$$(aws cloudformation describe-stacks $(AWS_OPTS) \
		--stack-name $(CP_STACK) \
		--query "Stacks[0].Outputs[?OutputKey=='CapacityProviderName'].OutputValue" \
		--output text) && \
	$(MAKE) -C .. scale-down CP_NAME=$$CP_NAME

scale-up-pause-strategy: ## Scale up the pause example CP
	@CP_NAME=$$(aws cloudformation describe-stacks $(AWS_OPTS) \
		--stack-name $(CP_STACK) \
		--query "Stacks[0].Outputs[?OutputKey=='CapacityProviderName'].OutputValue" \
		--output text) && \
	$(MAKE) -C .. scale-up CP_NAME=$$CP_NAME

cost-report-pause-strategy: ## Get cost report for the pause example CP
	@CP_NAME=$$(aws cloudformation describe-stacks $(AWS_OPTS) \
		--stack-name $(CP_STACK) \
		--query "Stacks[0].Outputs[?OutputKey=='CapacityProviderName'].OutputValue" \
		--output text) && \
	$(MAKE) -C .. cost-report CP_NAME=$$CP_NAME

register-shape-strategy: ## Register the shape example CP (30-min scale cycle)
	@CP_NAME=$$(aws cloudformation describe-stacks $(AWS_OPTS) \
		--stack-name $(CP_SHAPE_STACK) \
		--query "Stacks[0].Outputs[?OutputKey=='CapacityProviderName'].OutputValue" \
		--output text) && \
	$(MAKE) -C .. register CP_NAME=$$CP_NAME STRATEGY=shape \
		SCALE_DOWN_CRON="cron(0/30 * * * ? *)" \
		SCALE_UP_CRON="cron(15/30 * * * ? *)"

deregister-shape-strategy: ## Deregister the shape example CP
	@CP_NAME=$$(aws cloudformation describe-stacks $(AWS_OPTS) \
		--stack-name $(CP_SHAPE_STACK) \
		--query "Stacks[0].Outputs[?OutputKey=='CapacityProviderName'].OutputValue" \
		--output text) && \
	$(MAKE) -C .. deregister CP_NAME=$$CP_NAME

scale-down-shape-strategy: ## Scale down the shape example CP
	@CP_NAME=$$(aws cloudformation describe-stacks $(AWS_OPTS) \
		--stack-name $(CP_SHAPE_STACK) \
		--query "Stacks[0].Outputs[?OutputKey=='CapacityProviderName'].OutputValue" \
		--output text) && \
	$(MAKE) -C .. scale-down CP_NAME=$$CP_NAME

scale-up-shape-strategy: ## Scale up the shape example CP
	@CP_NAME=$$(aws cloudformation describe-stacks $(AWS_OPTS) \
		--stack-name $(CP_SHAPE_STACK) \
		--query "Stacks[0].Outputs[?OutputKey=='CapacityProviderName'].OutputValue" \
		--output text) && \
	$(MAKE) -C .. scale-up CP_NAME=$$CP_NAME

cost-report-shape-strategy: ## Get cost report for the shape example CP
	@CP_NAME=$$(aws cloudformation describe-stacks $(AWS_OPTS) \
		--stack-name $(CP_SHAPE_STACK) \
		--query "Stacks[0].Outputs[?OutputKey=='CapacityProviderName'].OutputValue" \
		--output text) && \
	$(MAKE) -C .. cost-report CP_NAME=$$CP_NAME

status: ## Show deployment status of all stacks
	@echo "==> Stack status:"
	@printf "  %-45s %s\n" "$(NETWORK_STACK)" \
		"$$(aws cloudformation describe-stacks $(AWS_OPTS) --stack-name $(NETWORK_STACK) \
			--query 'Stacks[0].StackStatus' --output text 2>/dev/null || echo 'NOT DEPLOYED')"
	@printf "  %-45s %s\n" "$(CP_STACK)" \
		"$$(aws cloudformation describe-stacks $(AWS_OPTS) --stack-name $(CP_STACK) \
			--query 'Stacks[0].StackStatus' --output text 2>/dev/null || echo 'NOT DEPLOYED')"
	@printf "  %-45s %s\n" "$(APP_STACK)" \
		"$$(aws cloudformation describe-stacks $(AWS_OPTS) --stack-name $(APP_STACK) \
			--query 'Stacks[0].StackStatus' --output text 2>/dev/null || echo 'NOT DEPLOYED')"
	@printf "  %-45s %s\n" "$(CP_SHAPE_STACK)" \
		"$$(aws cloudformation describe-stacks $(AWS_OPTS) --stack-name $(CP_SHAPE_STACK) \
			--query 'Stacks[0].StackStatus' --output text 2>/dev/null || echo 'NOT DEPLOYED')"
	@printf "  %-45s %s\n" "$(APP_SHAPE_STACK)" \
		"$$(aws cloudformation describe-stacks $(AWS_OPTS) --stack-name $(APP_SHAPE_STACK) \
			--query 'Stacks[0].StackStatus' --output text 2>/dev/null || echo 'NOT DEPLOYED')"
