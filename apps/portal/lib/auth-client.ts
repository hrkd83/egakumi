"use client";

import { createAuthClient } from "better-auth/react";

/** 同一オリジンの認証APIだけを呼び、クライアントシークレットを扱わないクライアントです。 */
export const authClient = createAuthClient({ basePath: "/api/auth" });
