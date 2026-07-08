import { SubmitForm } from "@/components/SubmitForm";

export default function SubmitPage() {
  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-col gap-1">
        <h1 className="text-2xl font-bold text-text-dark">
          Submit a grievance
        </h1>
        <p className="max-w-2xl text-sm text-text-secondary">
          Enter grievance text or upload a document. The demo extracts the
          text, redacts PII, classifies the grievance, summarises it, and
          proposes a routing. Results are illustrative — the processor is
          currently mocked.
        </p>
      </div>
      <SubmitForm />
    </div>
  );
}
