import React, { useCallback } from "react";
import { useLocalSearchParams, useRouter } from "expo-router";

import { ItemScreen } from "../src/screens/ItemScreen";
import { EmptyState } from "../src/ui/states";

export default function ItemRoute() {
  const router = useRouter();
  const { id, scenario } = useLocalSearchParams<{ id?: string; scenario?: string }>();
  const openTrace = useCallback(
    (period: string, target: string) => {
      router.push({ pathname: "/trace", params: { period, scenario: target } });
    },
    [router],
  );
  if (typeof id !== "string" || id.length === 0) {
    return <EmptyState title="Pick an item to open." example="what do I pay every month?" />;
  }
  return (
    <ItemScreen
      itemId={id}
      scenario={typeof scenario === "string" ? scenario : undefined}
      onBack={() => router.back()}
      onOpenEvents={(itemId) => router.push({ pathname: "/event", params: { item: itemId } })}
      onOpenTrace={openTrace}
    />
  );
}
