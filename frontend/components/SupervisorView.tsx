import type {
  AggregateCount,
  MockProvenance,
  SliceLabel,
  SupervisorDashboard,
  UnavailableProvenance,
} from "@/lib/supervisor";
import { Badge, Card } from "./ui";

function Slice({ value }: { value: SliceLabel }) {
  return (
    <dl className="grid grid-cols-1 gap-2 rounded-sm border border-hair bg-panel px-3 py-2 text-sm sm:grid-cols-3">
      {(
        [
          ["District", value.district],
          ["Category", value.category],
          ["Period", value.period],
        ] as const
      ).map(([label, text]) => (
        <div key={label}>
          <dt className="text-xs font-semibold uppercase tracking-wide text-maroon">
            {label}
          </dt>
          <dd>{text}</dd>
        </div>
      ))}
    </dl>
  );
}

function CountTile({ count }: { count: AggregateCount }) {
  return (
    <div className="rounded-sm border border-hair bg-surface p-3">
      <p className="text-xs font-semibold uppercase tracking-wide text-maroon">
        {count.label}
      </p>
      <p className="mt-1 text-3xl font-bold tabular-nums text-text-dark">
        {count.value.toLocaleString("en-IN")}
      </p>
      <p className="mt-1 text-xs leading-relaxed text-text-secondary">
        {count.explanation}
      </p>
    </div>
  );
}

function MockNotice({ provenance }: { provenance: MockProvenance }) {
  return (
    <div className="rounded-sm border border-maroon/40 bg-maroon/5 px-3 py-2 text-sm text-text-body">
      <Badge tone="maroon">{provenance.label}</Badge>{" "}
      {provenance.note}
    </div>
  );
}

function UnavailableNotice({
  provenance,
}: {
  provenance: UnavailableProvenance;
}) {
  return (
    <div className="rounded-sm border border-hair bg-panel px-3 py-2 text-sm text-text-body">
      <Badge tone="neutral">{provenance.label}</Badge>{" "}
      {provenance.reason}
    </div>
  );
}

function ClosureFinding({ data }: { data: SupervisorDashboard["closure"] }) {
  if ("primarySharePct" in data) {
    return (
      <div className="flex flex-col gap-3">
        <div className="rounded-sm border border-hair bg-panel px-3 py-2 text-sm text-text-body">
          <Badge tone="neutral">{data.provenance.label}</Badge>{" "}
          As of {data.provenance.asOf} · source: {data.provenance.source}
        </div>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <div className="rounded-sm border border-hair bg-surface p-3">
            <p className="text-xs font-semibold uppercase tracking-wide text-maroon">
              Share of templated closures
            </p>
            <p className="mt-1 text-3xl font-bold tabular-nums text-text-dark">
              {data.primarySharePct.toFixed(1)}%
            </p>
            <p className="mt-1 text-xs text-text-secondary">
              {data.numerator.toLocaleString("en-IN")} {data.numeratorLabel.toLowerCase()}{" "}
              / {data.primaryDenominator.toLocaleString("en-IN")} {data.primaryDenominatorLabel.toLowerCase()}
            </p>
          </div>
          <div className="rounded-sm border border-hair bg-panel p-3">
            <p className="text-xs font-semibold uppercase tracking-wide text-maroon">
              Same numerator, all resolved
            </p>
            <p className="mt-1 text-3xl font-bold tabular-nums text-text-dark">
              {data.secondarySharePct.toFixed(1)}%
            </p>
            <p className="mt-1 text-xs text-text-secondary">
              {data.numerator.toLocaleString("en-IN")} /{" "}
              {data.secondaryDenominator.toLocaleString("en-IN")} {data.secondaryDenominatorLabel.toLowerCase()}
            </p>
          </div>
        </div>
        <p className="rounded-sm border border-hair bg-panel px-3 py-2 text-sm leading-relaxed text-text-body">
          <strong>Caveat:</strong> {data.caveat}
        </p>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-3">
      <UnavailableNotice provenance={data.provenance} />
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <div className="rounded-sm border border-hair bg-surface p-3">
          <p className="text-xs font-semibold uppercase tracking-wide text-maroon">
            Headline share
          </p>
          <p className="mt-1 text-3xl font-bold text-text-secondary">—</p>
          <p className="mt-1 text-xs text-text-secondary">
            No numerator, denominator, as-of date, and source are available
            together, so no figure is rendered.
          </p>
        </div>
        <dl className="rounded-sm border border-hair bg-panel p-3 text-sm">
          <dt className="text-xs font-semibold uppercase tracking-wide text-maroon">
            Numerator required
          </dt>
          <dd className="mb-3 text-text-body">{data.numeratorLabel}</dd>
          <dt className="text-xs font-semibold uppercase tracking-wide text-maroon">
            Denominator beside headline
          </dt>
          <dd className="mb-3 text-text-body">
            {data.primaryDenominatorLabel}
          </dd>
          <dt className="text-xs font-semibold uppercase tracking-wide text-maroon">
            Comparison denominator
          </dt>
          <dd className="text-text-body">{data.secondaryDenominatorLabel}</dd>
        </dl>
      </div>
      <p className="rounded-sm border border-hair bg-panel px-3 py-2 text-sm leading-relaxed text-text-body">
        <strong>Caveat:</strong> {data.caveat}
      </p>
    </div>
  );
}

export function SupervisorView({ data }: { data: SupervisorDashboard }) {
  return (
    <div className="flex flex-col gap-5">
      <div className="rounded-md border border-hair bg-card px-4 py-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="text-sm font-semibold text-text-dark">
            {data.generatedLabel}
          </p>
          <Badge tone="neutral">aggregate only</Badge>
        </div>
        <p className="mt-1 text-xs leading-relaxed text-text-secondary">
          {data.safetyNote}
        </p>
      </div>

      <Card title={data.workload.title} hint={data.workload.kind}>
        <div className="flex flex-col gap-3">
          <div>
            <Badge tone="maroon">{data.workload.kind}</Badge>
          </div>
          <MockNotice provenance={data.workload.provenance} />
          <Slice value={data.workload.slice} />
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            <CountTile count={data.workload.totalFilings} />
            <CountTile count={data.workload.distinctProblems} />
            <CountTile count={data.workload.duplicateAdjustment} />
          </div>
          <p className="text-sm leading-relaxed text-text-body">
            This slice contains{" "}
            <strong>{data.workload.totalFilings.value.toLocaleString("en-IN")}</strong>{" "}
            filings representing{" "}
            <strong>
              {data.workload.distinctProblems.value.toLocaleString("en-IN")}
            </strong>{" "}
            distinct problems. The adjustment changes the workload reading; it
            does not suppress repeat grievances or campaigns.
          </p>
        </div>
      </Card>

      <Card title={data.spike.title} hint={data.spike.kind}>
        <div className="flex flex-col gap-3">
          <div>
            <Badge tone="maroon">{data.spike.kind}</Badge>
          </div>
          <MockNotice provenance={data.spike.provenance} />
          <Slice value={data.spike.slice} />
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            {data.spike.counts.map((count) => (
              <CountTile key={count.label} count={count} />
            ))}
          </div>
          <div className="border-l-4 border-maroon bg-panel px-3 py-2">
            <p className="text-xs font-semibold uppercase tracking-wide text-maroon">
              How to read it
            </p>
            <p className="mt-1 text-sm leading-relaxed text-text-body">
              {data.spike.interpretation}
            </p>
          </div>
        </div>
      </Card>

      <Card title={data.closure.title} hint={data.closure.kind}>
        <div className="flex flex-col gap-3">
          <div>
            <Badge tone="neutral">{data.closure.kind}</Badge>
          </div>
          <ClosureFinding data={data.closure} />
        </div>
      </Card>
    </div>
  );
}
