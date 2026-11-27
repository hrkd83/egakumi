"""Make the API Gateway deployment modules available to each publication test."""

import sys

from tests import REPOSITORY


sys.path.insert(0, str(REPOSITORY / "apps/api-gateway/deploy"))
