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
  const openForecast = useCallback(() => router.push("/forecast"), [router]);
  return <HomeScreen onOpenTrace={openTrace} onOpenForecast={openForecast} />;
}
