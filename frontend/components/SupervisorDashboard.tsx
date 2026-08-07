"use client";

import { useEffect, useState } from "react";
import { fetchSupervisorDashboard } from "@/lib/api";
import {
  unavailableSupervisorDashboard,
  type SupervisorDashboard,
} from "@/lib/supervisor";
import { SupervisorView } from "./SupervisorView";

const CONNECTING_REASON =
  "Waiting for the aggregate service to return a validated response.";
const UNAVAILABLE_REASON =
  "The aggregate service did not return a usable aggregate-only response.";

export function SupervisorDashboard() {
  const [dashboard, setDashboard] = useState<SupervisorDashboard>(() =>
    unavailableSupervisorDashboard(CONNECTING_REASON),
  );
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    fetchSupervisorDashboard()
      .then((response) => {
        if (!cancelled) setDashboard(response);
      })
      .catch(() => {
        if (!cancelled) {
          setDashboard(unavailableSupervisorDashboard(UNAVAILABLE_REASON));
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="flex flex-col gap-3">
      {loading ? (
        <p
          role="status"
          className="text-sm text-text-secondary"
        >
          Checking aggregate finding availability…
        </p>
      ) : null}
      <SupervisorView data={dashboard} />
    </div>
  );
}
