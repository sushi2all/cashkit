import React, { useCallback } from "react";
import { useLocalSearchParams, useRouter } from "expo-router";

import { ForecastScreen } from "../src/screens/ForecastScreen";

export default function ForecastRoute() {
  const router = useRouter();
  const { scenario } = useLocalSearchParams<{ scenario?: string }>();
  const openTrace = useCallback(
    (period: string, target: string) => {
      router.push({ pathname: "/trace", params: { period, scenario: target } });
    },
    [router],
  );
  return (
    <ForecastScreen
      scenario={typeof scenario === "string" ? scenario : undefined}
      onOpenTrace={openTrace}
      onBack={() => router.back()}
    />
  );
}
