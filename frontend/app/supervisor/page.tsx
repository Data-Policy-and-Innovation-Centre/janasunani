import { SupervisorView } from "@/components/SupervisorView";
import { MOCK_SUPERVISOR_RESPONSE } from "@/lib/supervisor";

export default function SupervisorPage() {
  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-col gap-1">
        <h1 className="text-2xl font-bold text-text-dark">
          Supervisor briefing
        </h1>
        <p className="max-w-3xl text-sm leading-relaxed text-text-secondary">
          Three headline measures for a selected slice: duplicate-adjusted
          workload, a spike decomposed into three counts, and the closure
          finding on its stated denominator.
        </p>
      </div>
      <SupervisorView data={MOCK_SUPERVISOR_RESPONSE} />
    </div>
  );
}
