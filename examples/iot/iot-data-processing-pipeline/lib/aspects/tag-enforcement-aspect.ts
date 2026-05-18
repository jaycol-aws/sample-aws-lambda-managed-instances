import * as cdk from 'aws-cdk-lib';
import { IAspect } from 'aws-cdk-lib';
import { IConstruct } from 'constructs';

/**
 * Required tags that must be present on all CloudFormation resources.
 */
const REQUIRED_TAGS = ['Environment', 'Project', 'CostCenter'] as const;

/**
 * CDK Aspect that enforces required tags on all CfnResource nodes.
 *
 * Visits every CfnResource in the construct tree and adds an error
 * annotation if any of the required tags (Environment, Project, CostCenter)
 * are missing.
 */
export class TagEnforcementAspect implements IAspect {
  public visit(node: IConstruct): void {
    if (node instanceof cdk.CfnResource) {
      // CfnResource.tags may be undefined for resources that don't support tagging
      const tagManager = (node as any).tags as cdk.TagManager | undefined;
      if (!tagManager) {
        return;
      }

      const renderedTags: Array<Record<string, string>> =
        tagManager.renderTags() ?? [];

      // renderTags() may return { key, value } (lowercase) or { Key, Value } (uppercase)
      // depending on the CDK version and resource type
      const tagKeys = new Set(renderedTags.map((t) => t.Key ?? t.key));

      for (const required of REQUIRED_TAGS) {
        if (!tagKeys.has(required)) {
          cdk.Annotations.of(node).addError(
            `Missing required tag '${required}' on resource ${node.node.path} (${node.cfnResourceType})`,
          );
        }
      }
    }
  }
}
