import React, { useCallback } from "react";
import { useRouter } from "expo-router";

import { ScenariosScreen } from "../src/screens/ScenariosScreen";

export default function ScenariosRoute() {
  const router = useRouter();
  const openTrace = useCallback(
    (period: string, scenario: string) => {
      router.push({ pathname: "/trace", params: { period, scenario } });
    },
    [router],
  );
  return <ScenariosScreen onOpenTrace={openTrace} onBack={() => router.back()} />;
}
