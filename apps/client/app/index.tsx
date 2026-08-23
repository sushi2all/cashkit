import React, { useCallback } from "react";
import { useRouter } from "expo-router";

import { HomeScreen } from "../src/screens/HomeScreen";

export default function HomeRoute() {
  const router = useRouter();
  const openTrace = useCallback(
    (period: string, scenario: string) => {
      router.push({ pathname: "/trace", params: { period, scenario } });
    },
    [router],
  );
  return (
    <HomeScreen
      onOpenTrace={openTrace}
      onOpenForecast={() => router.push("/forecast")}
      onOpenScenarios={() => router.push("/scenarios")}
      onOpenActuals={() => router.push("/actuals")}
      onOpenPlan={() => router.push("/plan")}
      onOpenSettings={() => router.push("/settings")}
    />
  );
}
