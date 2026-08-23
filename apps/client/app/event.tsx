import React from "react";
import { useLocalSearchParams, useRouter } from "expo-router";

import { EventScreen } from "../src/screens/EventScreen";

export default function EventRoute() {
  const router = useRouter();
  const { id, item, scenario } = useLocalSearchParams<{
    id?: string;
    item?: string;
    scenario?: string;
  }>();
  return (
    <EventScreen
      eventId={typeof id === "string" ? id : undefined}
      itemId={typeof item === "string" ? item : undefined}
      scenario={typeof scenario === "string" ? scenario : undefined}
      onBack={() => router.back()}
      onOpenActuals={() => router.push("/actuals")}
    />
  );
}
