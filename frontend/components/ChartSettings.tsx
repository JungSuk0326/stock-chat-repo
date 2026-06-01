"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import {
  GROUP_ORDER,
  INDICATOR_GROUPS,
  type IndicatorId,
} from "@/lib/chartIndicators";

interface Props {
  selected: Set<IndicatorId>;
  onChange: (next: Set<IndicatorId>) => void;
}

/**
 * Gear button + popover with grouped indicator checkboxes. Selection
 * state is owned by the parent (PriceChart) so the chart re-renders the
 * series when toggled.
 *
 * Popover closes on outside click and ESC. No portal — its absolute
 * positioning sits inside the chart card.
 */
export function ChartSettings({ selected, onChange }: Props) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    const handleClickOutside = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    const handleEsc = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("mousedown", handleClickOutside);
    window.addEventListener("keydown", handleEsc);
    return () => {
      window.removeEventListener("mousedown", handleClickOutside);
      window.removeEventListener("keydown", handleEsc);
    };
  }, [open]);

  const toggle = useCallback(
    (id: IndicatorId) => {
      const next = new Set(selected);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      onChange(next);
    },
    [selected, onChange],
  );

  return (
    <div ref={rootRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-label="차트 지표 설정"
        title="차트 지표 설정"
        className="inline-flex items-center gap-1 rounded border border-gray-300 bg-white px-2 py-1 text-xs text-gray-700 hover:bg-gray-50"
      >
        <GearIcon />
        <span>지표</span>
        {selected.size > 0 && (
          <span className="ml-1 rounded-full bg-blue-100 px-1.5 text-blue-700">
            {selected.size}
          </span>
        )}
      </button>

      {open && (
        <div
          className="absolute right-0 z-20 mt-1 w-64 rounded-lg border border-gray-200 bg-white p-3 shadow-lg"
          role="dialog"
        >
          {GROUP_ORDER.map((group) => (
            <div key={group} className="mb-3 last:mb-0">
              <h3 className="mb-1 text-xs font-semibold text-gray-500">
                {group}
              </h3>
              <ul className="space-y-1">
                {INDICATOR_GROUPS[group]?.map((ind) => (
                  <li key={ind.id}>
                    <label className="flex cursor-pointer items-center gap-2 text-sm text-gray-900">
                      <input
                        type="checkbox"
                        checked={selected.has(ind.id)}
                        onChange={() => toggle(ind.id)}
                        className="h-4 w-4 rounded border-gray-300 text-blue-600 focus:ring-blue-500"
                      />
                      <span
                        className="inline-block h-2 w-3 rounded-sm"
                        style={{ backgroundColor: ind.color }}
                      />
                      {ind.label}
                    </label>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function GearIcon() {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 24 24"
      fill="currentColor"
      className="h-3.5 w-3.5"
      aria-hidden="true"
    >
      <path
        fillRule="evenodd"
        d="M11.078 2.25c-.917 0-1.699.663-1.85 1.567L9.05 4.889c-.02.122-.103.228-.223.273a7.49 7.49 0 0 0-.95.392c-.115.058-.252.054-.357-.007l-1.066-.615a1.875 1.875 0 0 0-2.46.518l-.823 1.428a1.875 1.875 0 0 0 .487 2.471l.972.737c.103.077.16.2.149.327a7.6 7.6 0 0 0 0 1.024c.011.127-.046.25-.149.327l-.972.737a1.875 1.875 0 0 0-.487 2.47l.824 1.428a1.875 1.875 0 0 0 2.459.519l1.066-.615c.105-.061.242-.065.357-.007.303.151.62.282.95.392.12.045.203.151.223.273l.178 1.071c.151.904.933 1.567 1.85 1.567h1.844c.917 0 1.699-.663 1.85-1.567l.178-1.072c.02-.12.103-.226.223-.27.331-.111.65-.241.952-.392.114-.059.251-.054.356.006l1.066.615a1.875 1.875 0 0 0 2.459-.518l.823-1.428a1.875 1.875 0 0 0-.487-2.471l-.973-.737a.357.357 0 0 1-.149-.327 7.6 7.6 0 0 0 0-1.024c-.011-.127.046-.25.149-.327l.973-.737a1.875 1.875 0 0 0 .486-2.47l-.823-1.429a1.875 1.875 0 0 0-2.46-.518l-1.065.615a.36.36 0 0 1-.357.007 7.5 7.5 0 0 0-.951-.392.36.36 0 0 1-.223-.273l-.179-1.071a1.875 1.875 0 0 0-1.85-1.567h-1.843ZM12 15.75a3.75 3.75 0 1 0 0-7.5 3.75 3.75 0 0 0 0 7.5Z"
        clipRule="evenodd"
      />
    </svg>
  );
}
