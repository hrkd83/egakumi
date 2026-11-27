/** 委任認証の用途は通常の同一オリジン操作でのみ指定します。 */
export const subscriptionManagementPurpose = "subscription-management";
export const apiAuthPurposeHeader = "x-egakumi-api-auth-purpose";
export const subscriptionManagementRequiredCode = "SubscriptionManagementAuthenticationRequired";
export const subscriptionManagementRequiredHeader = "x-egakumi-subscription-auth";
export type ApiAuthPurpose = "gateway" | typeof subscriptionManagementPurpose;
