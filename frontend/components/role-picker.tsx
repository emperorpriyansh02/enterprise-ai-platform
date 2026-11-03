"use client";

import { ROLE_LABELS, ROLES, type RoleName } from "@/lib/api";

/** "Who can use it": tick roles, or none for everyone. Admins can always use everything. */
export default function RolePicker({
  value,
  onChange,
  disabled = false,
  label = "Who can use it",
}: {
  value: RoleName[];
  onChange: (roles: RoleName[]) => void;
  disabled?: boolean;
  label?: string;
}) {
  return (
    <fieldset className="text-sm" disabled={disabled}>
      <legend className="mb-1 block text-xs font-medium text-muted">{label}</legend>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
        {ROLES.map((role) => (
          <label key={role} className="flex items-center gap-1.5">
            <input
              type="checkbox"
              checked={value.includes(role)}
              onChange={(e) =>
                onChange(e.target.checked ? [...value, role] : value.filter((r) => r !== role))
              }
            />
            {ROLE_LABELS[role]}
          </label>
        ))}
        <span className="text-xs text-muted">
          {value.length === 0 ? "None ticked = everyone." : "Admins always have access."}
        </span>
      </div>
    </fieldset>
  );
}

/** "Everyone", or "Editor, User". */
export function rolesLabel(roles: RoleName[]): string {
  return roles.length === 0 ? "Everyone" : roles.map((r) => ROLE_LABELS[r] ?? r).join(", ");
}
