import type { HistoryEvent } from "@/lib/types";
import { EmptyNote, formatTimestamp, titleCase } from "./ui";

export function CaseTimeline({ events, note }: { events: HistoryEvent[]; note?: string }) {
  if (events.length === 0) {
    return <EmptyNote>No timeline events are available for this case yet.</EmptyNote>;
  }

  return (
    <div>
      <ol className="flex flex-col gap-0">
        {events.map((event, i) => (
          <li key={i} className="relative flex gap-3 pb-4 pl-1 last:pb-0">
            {i < events.length - 1 ? (
              <span className="absolute left-[7px] top-3 h-full w-px bg-border-strong" />
            ) : null}
            <span className="relative mt-1 h-3 w-3 shrink-0 rounded-full border-2 border-accent bg-surface" />
            <div>
              <div className="flex flex-wrap items-baseline gap-2">
                <span className="text-sm font-semibold text-muted-strong">{titleCase(event.event_type)}</span>
                <span className="text-[11px] text-muted">{formatTimestamp(event.occurred_at)}</span>
              </div>
              <p className="mt-0.5 text-xs text-muted">{event.description}</p>
            </div>
          </li>
        ))}
      </ol>
      {note ? <p className="mt-2 text-[11px] text-muted">{note}</p> : null}
    </div>
  );
}
