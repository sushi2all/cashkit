import React from "react";
import { useRouter } from "expo-router";

import { ImportExportScreen } from "../src/screens/ImportExportScreen";

export default function ImportRoute() {
  const router = useRouter();
  return <ImportExportScreen onBack={() => router.back()} />;
}
