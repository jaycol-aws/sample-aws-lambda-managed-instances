import * as cdk from 'aws-cdk-lib';
import { IAspect } from 'aws-cdk-lib';
import { IConstruct } from 'constructs';

/**
 * Checks if a value is either a resolved truthy value or an unresolved CDK Token.
 * During aspect visitation, KMS key references appear as Token objects rather than
 * final ARN strings. This helper accepts both cases as "configured".
 */
function isTokenOrTruthy(value: unknown): boolean {
  if (value === null || value === undefined) return false;
  if (cdk.Token.isUnresolved(value)) return true;
  return !!value;
}

/**
 * Map of CloudFormation resource types to the check function for KMS encryption.
 *
 * IMPORTANT: cfnProperties uses camelCase property names (e.g., streamEncryption,
 * encryptionType, keyId) — NOT the PascalCase used in final CloudFormation output.
 */
const KMS_ENCRYPTION_CHECKS: Record<
  string,
  { check: (props: any) => boolean; description: string }
> = {
  'AWS::Kinesis::Stream': {
    check: (props) =>
      props?.streamEncryption?.encryptionType === 'KMS' &&
      isTokenOrTruthy(props?.streamEncryption?.keyId),
    description: 'Kinesis stream must use KMS encryption',
  },
  'AWS::DynamoDB::Table': {
    check: (props) =>
      props?.sseSpecification?.sseEnabled === true ||
      isTokenOrTruthy(props?.sseSpecification?.kmsMasterKeyId),
    description: 'DynamoDB table must have SSE enabled',
  },
  'AWS::S3::Bucket': {
    check: (props) => {
      const rules =
        props?.bucketEncryption?.serverSideEncryptionConfiguration;
      if (!Array.isArray(rules) || rules.length === 0) return false;
      return rules.some(
        (rule: any) =>
          rule?.serverSideEncryptionByDefault?.sseAlgorithm === 'aws:kms' ||
          isTokenOrTruthy(rule?.serverSideEncryptionByDefault?.kmsMasterKeyId),
      );
    },
    description: 'S3 bucket must use SSE-KMS encryption',
  },
  'AWS::SQS::Queue': {
    check: (props) => isTokenOrTruthy(props?.kmsMasterKeyId),
    description: 'SQS queue must use KMS encryption',
  },
  'AWS::SNS::Topic': {
    check: (props) => isTokenOrTruthy(props?.kmsMasterKeyId),
    description: 'SNS topic must use KMS encryption',
  },
};

/**
 * CDK Aspect that enforces security policies across the stack.
 *
 * Checks:
 * 1. No public subnets exist in VPCs.
 * 2. KMS encryption is configured on supported resources
 *    (Kinesis, DynamoDB, S3, SQS, SNS).
 *
 * Note: CDK Aspects visit the construct tree before token resolution.
 * The cfnProperties accessor returns camelCase property names with CDK
 * Token placeholders for cross-resource references. The checks account
 * for both resolved values and unresolved Token presence.
 */
export class SecurityPolicyAspect implements IAspect {
  public visit(node: IConstruct): void {
    if (!(node instanceof cdk.CfnResource)) {
      return;
    }

    this.checkPublicSubnets(node);
    this.checkKmsEncryption(node);
  }

  /**
   * Flags VPC subnets that have MapPublicIpOnLaunch enabled,
   * indicating a public subnet.
   */
  private checkPublicSubnets(node: cdk.CfnResource): void {
    if (node.cfnResourceType === 'AWS::EC2::Subnet') {
      const props = (node as any).cfnProperties ?? {};
      if (props.mapPublicIpOnLaunch === true) {
        cdk.Annotations.of(node).addError(
          `Public subnet detected at ${node.node.path}. ` +
            'VPCs must use only private subnets (no public subnets).',
        );
      }
    }
  }

  /**
   * Verifies that resources which support KMS encryption have it configured.
   */
  private checkKmsEncryption(node: cdk.CfnResource): void {
    const check = KMS_ENCRYPTION_CHECKS[node.cfnResourceType];
    if (!check) {
      return;
    }

    const props = (node as any).cfnProperties ?? {};
    if (!check.check(props)) {
      cdk.Annotations.of(node).addWarning(
        `${check.description} at ${node.node.path} (${node.cfnResourceType})`,
      );
    }
  }
}
