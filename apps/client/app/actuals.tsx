import React from "react";
import { useRouter } from "expo-router";

import { ActualsScreen } from "../src/screens/ActualsScreen";

export default function ActualsRoute() {
  const router = useRouter();
  return <ActualsScreen onBack={() => router.back()} onOpenPlan={() => router.push("/plan")} />;
}
