import React, { useCallback } from "react";
import { useRouter } from "expo-router";

import { PlanVsActualScreen } from "../src/screens/PlanVsActualScreen";

export default function PlanRoute() {
  const router = useRouter();
  const openTrace = useCallback(
    (period: string, scenario: string) => {
      router.push({ pathname: "/trace", params: { period, scenario } });
    },
    [router],
  );
  return <PlanVsActualScreen onBack={() => router.back()} onOpenTrace={openTrace} />;
}
