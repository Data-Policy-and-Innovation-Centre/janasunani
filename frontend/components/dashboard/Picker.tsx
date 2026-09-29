/** A labelled dropdown for one scope: entry office or filing year. The empty
 * value is the whole of it (statewide, all years). */
export function Picker({ label, allLabel, options, value, onChange }: {
  label: string;
  allLabel: string;
  options: { id: string; label: string }[];
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <label className="flex items-center gap-3">
      <span className="text-[15px] text-text-secondary">{label}</span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="rounded border border-hair bg-surface px-3 py-2 text-[17px] text-text-dark focus-visible:outline-2 focus-visible:outline-maroon"
      >
        <option value="">{allLabel}</option>
        {options.map((o) => (
          <option key={o.id} value={o.id}>
            {o.label}
          </option>
        ))}
      </select>
    </label>
  );
}
