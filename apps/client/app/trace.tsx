import React from "react";
import { useLocalSearchParams, useRouter } from "expo-router";

import { TraceScreen } from "../src/screens/TraceScreen";
import { EmptyState } from "../src/ui/states";

export default function TraceRoute() {
  const router = useRouter();
  const { period, scenario } = useLocalSearchParams<{ period?: string; scenario?: string }>();
  if (typeof period !== "string" || period.length === 0) {
    return <EmptyState title="Pick a month to trace." example="show me the forecast" />;
  }
  return (
    <TraceScreen
      period={period}
      scenario={typeof scenario === "string" ? scenario : undefined}
      onBack={() => router.back()}
    />
  );
}
