/**
 * Screen 3 — Forecast (SPEC §6-S3, §5-F3).
 *
 * The MLP forecast is the designed monthly view: a chart card and a
 * MONTH / IN / OUT / END table with a summary strip. There is no item×month
 * grid in the MLP. Tapping a row opens the month-scoped Trace, which is the
 * MLP carrier of the ADR-0013 cell taxonomy.
 *
 * Every figure in the table is a service string rendered verbatim. The screen
 * adds no totals of its own — `summary` arrives computed, and a row's `net` is
 * the engine's, not `inflow` minus `outflow` worked out here.
 */
import React, { useCallback, useEffect, useState } from "react";
import { ScrollView, Text, View, StyleSheet } from "react-native";

import type { Forecast } from "@cashkit/api-types";

import { api, describeError } from "../api/client";
import { formatBare, formatMoney, moneyTone } from "../money/money";
import { Card, Divider, Stamp } from "../ui/atoms";
import { monthLabel, shortDate, WhatIfStamp } from "../ui/provenance";
import { EmptyState, ErrorState, LoadingState } from "../ui/states";
import { color, font, space } from "../ui/tokens";
import { ForecastChart } from "./components/Sparkline";
import { WarningsBanner } from "./components/WarningsBanner";

export function ForecastScreen({
  scenario,
  onOpenTrace,
  onBack,
  testID = "forecast-screen",
}: {
  scenario?: string;
  onOpenTrace: (period: string, scenario: string) => void;
  onBack: () => void;
  testID?: string;
}) {
  const [forecast, setForecast] = useState<Forecast | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    const { data, error: err, response } = await api.GET("/book/forecast", {
      params: { query: scenario ? { scenario } : {} },
    });
    if (err || !data) {
      setForecast(null);
      setError(describeError(err, response.status));
    } else {
      setForecast(data);
      setError(null);
    }
    setLoading(false);
  }, [scenario]);

  useEffect(() => {
    void load();
  }, [load]);

  if (loading && !forecast) return <LoadingState label="Running the forecast…" />;
  if (error) return <ErrorState message={error} onRetry={() => void load()} testID={`${testID}-error`} />;
  if (!forecast) return <EmptyState title="No forecast yet." example="I earn 3,000 a month" />;

  const negativePeriods = new Set(forecast.warnings.negative_months.map((m) => m.period.slice(0, 7)));
  const minPeriod = forecast.summary.min_cash_period?.slice(0, 7) ?? null;
  const months = forecast.rows.map((row) => row.period);

  return (
    <View testID={testID} style={styles.screen}>
      <View style={styles.headerRow}>
        <Text testID={`${testID}-back`} style={styles.back} onPress={onBack}>
          ‹ BACK
        </Text>
        <Text style={styles.title}>Forecast</Text>
        <View style={styles.chip}>
          <Text testID={`${testID}-scenario`} style={styles.chipLabel}>
            {forecast.scenario}
          </Text>
        </View>
      </View>

      <Text testID={`${testID}-subline`} style={styles.subline}>
        {`${forecast.rows.length} ${forecast.grain.toUpperCase()}S · ${
          forecast.window[0] ? monthLabel(forecast.window[0]) : "—"
        } – ${
          forecast.window[forecast.window.length - 1]
            ? monthLabel(forecast.window[forecast.window.length - 1] as string)
            : "—"
        } · AS-OF ${shortDate(forecast.as_of)}`}
      </Text>
      <WhatIfStamp whatIf={forecast.what_if} testID={`${testID}-what-if`} />

      <WarningsBanner warnings={forecast.warnings} testID={`${testID}-warnings`} />

      <ScrollView style={styles.body} contentContainerStyle={styles.bodyContent}>
        <Card testID={`${testID}-chart-card`}>
          <ForecastChart
            closing={forecast.rows.map((row) => row.closing)}
            months={months}
            cutover={null}
            minCash={forecast.summary.min_cash}
            minCashPeriod={forecast.summary.min_cash_period}
            testID={`${testID}-chart`}
          />
        </Card>

        <Card testID={`${testID}-table-card`} style={styles.tableCard}>
          <View style={styles.headRow}>
            {["MONTH", "IN", "OUT", "END"].map((heading) => (
              <View key={heading} style={styles.cell}>
                <Text style={styles.headCell}>{heading}</Text>
              </View>
            ))}
          </View>

          {forecast.rows.map((row) => {
            const key = row.period.slice(0, 7);
            const isMin = minPeriod === key;
            const isNegative = negativePeriods.has(key);
            return (
              <View key={row.period}>
                <Divider />
                <Text
                  testID={`${testID}-row-${key}`}
                  accessibilityRole="button"
                  onPress={() => onOpenTrace(row.period, forecast.scenario)}
                  style={[styles.rowPress, (isMin || isNegative) && styles.rowHighlight]}
                >
                  <View style={styles.dataRow}>
                    <View style={styles.cell}>
                      <Text style={[styles.cellMonth, (isMin || isNegative) && styles.cellStrong]}>
                        {monthLabel(row.period)}
                      </Text>
                    </View>
                    <View style={styles.cell}>
                      <Text style={[styles.cellValue, { color: color.pine }]}>{formatBare(row.inflow)}</Text>
                    </View>
                    <View style={styles.cell}>
                      <Text style={[styles.cellValue, { color: color.sub }]}>{formatBare(row.outflow)}</Text>
                    </View>
                    <View style={styles.cell}>
                      <Text
                        testID={`${testID}-row-${key}-end`}
                        style={[styles.cellValue, styles.cellStrong, { color: color[moneyTone(row.closing)] }]}
                      >
                        {formatBare(row.closing)}
                      </Text>
                    </View>
                  </View>
                </Text>
                {isMin ? (
                  <View style={styles.noteRow}>
                    <Stamp tone="rust" testID={`${testID}-row-${key}-note`}>
                      {`MIN ${formatMoney(forecast.summary.min_cash)} · ${monthLabel(row.period)}`}
                    </Stamp>
                  </View>
                ) : null}
              </View>
            );
          })}
        </Card>

        <View style={styles.summaryStrip}>
          <Stamp>{`IN ${formatMoney(forecast.summary.total_inflow)}`}</Stamp>
          <Stamp>{`OUT ${formatMoney(forecast.summary.total_outflow)}`}</Stamp>
          <Stamp>{`NET ${formatMoney(forecast.summary.net_cash)}`}</Stamp>
        </View>
      </ScrollView>

      <View style={styles.footer}>
        <Stamp testID={`${testID}-footer-note`}>ALL FIGURES COMPUTED · TAP A ROW FOR THE TRACE</Stamp>
        <Stamp testID={`${testID}-provenance`}>
          {`ENGINE v${forecast.engine_version} · REV ${
            forecast.revision ? forecast.revision.slice(0, 7) : "UNCOMMITTED"
          }`}
        </Stamp>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  screen: {
    flex: 1,
    backgroundColor: color.paper,
    paddingHorizontal: space.screenX,
    paddingTop: space.screenTop,
    paddingBottom: space.screenBottom,
    gap: 8,
  },
  headerRow: { flexDirection: "row", alignItems: "center", gap: 12 },
  back: { fontFamily: font.mono, fontSize: 10, letterSpacing: 0.7, color: color.sub },
  title: { fontFamily: font.display, fontSize: 28, fontWeight: "600", color: color.ink, flex: 1 },
  chip: { backgroundColor: color.pineTint, borderRadius: 999, paddingVertical: 8, paddingHorizontal: 14 },
  chipLabel: { fontFamily: font.ui, fontSize: 13, fontWeight: "600", color: color.pine },
  subline: { fontFamily: font.mono, fontSize: 9.5, letterSpacing: 0.6, color: color.sub },
  body: { flex: 1 },
  bodyContent: { gap: 14, paddingBottom: 12 },
  tableCard: { padding: 0, gap: 0 },
  headRow: { flexDirection: "row", paddingHorizontal: 16, paddingTop: 12, paddingBottom: 8 },
  dataRow: { flexDirection: "row", width: "100%" },
  rowPress: { paddingHorizontal: 16, paddingVertical: 11 },
  rowHighlight: { backgroundColor: color.pineTint },
  cell: { flex: 1 },
  headCell: { fontFamily: font.mono, fontSize: 9, color: color.faint },
  cellMonth: { fontFamily: font.ui, fontSize: 13, color: color.sub },
  cellValue: { fontFamily: font.ui, fontSize: 13 },
  cellStrong: { fontWeight: "600", color: color.ink },
  noteRow: { paddingHorizontal: 16, paddingBottom: 10, backgroundColor: color.pineTint },
  summaryStrip: { flexDirection: "row", justifyContent: "space-between", width: "100%" },
  footer: { gap: 4 },
});
