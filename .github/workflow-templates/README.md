# Deployment workflow examples

These `.yml.example` files are disabled setup templates. They do not run in GitHub Actions as distributed.

Configure an environment you own, a private-network runner with your chosen label, the listed GitHub environment variables, Azure OIDC trust and scoped deployment permissions. Then copy the required template into `.github/workflows/` with a `.yml` extension. Review the Portal push trigger before enabling it. `example-vnet` is a placeholder runner label.

The templates contain no account IDs, authentication secrets or organization runner identifiers. Local deployment commands remain available through `scripts/deploy_gateway.py` and `scripts/deploy_portal.py`.
