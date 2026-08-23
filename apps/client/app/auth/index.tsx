import React, { useCallback } from "react";
import { useLocalSearchParams, useRouter } from "expo-router";

import { AuthScreen } from "../../src/screens/AuthScreen";
import { useSession } from "../../src/state/session";

export default function AuthRoute() {
  const { requestLink } = useSession();
  const router = useRouter();
  const { error } = useLocalSearchParams<{ error?: string }>();
  const dismiss = useCallback(() => {
    if (error) router.setParams({ error: undefined });
  }, [error, router]);

  return (
    <AuthScreen
      onRequestLink={requestLink}
      initialError={typeof error === "string" ? error : null}
      onDismissError={dismiss}
    />
  );
}
