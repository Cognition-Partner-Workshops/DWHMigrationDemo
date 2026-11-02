import { useEffect, useState } from "react";

import { getResources, updateResourceAvailability, type ResourceItem } from "./api";
import { CheckIcon, InfoIcon, WarningIcon } from "./Icons";

function ResourceGroup({
  title,
  subtitle,
  items,
  updatingId,
  onChange,
}: {
  title: string;
  subtitle: string;
  items: ResourceItem[];
  updatingId: string | null;
  onChange: (item: ResourceItem, available: boolean) => void;
}) {
  const required = title === "Required";
  return (
    <section className={`resource-category ${required ? "required" : "optional"}`}>
      <header>
        {required ? <WarningIcon size={13} /> : <InfoIcon size={13} />}
        <strong>{title}</strong>
        <span>{subtitle}</span>
      </header>
      <div className="resource-table-head">
        <span>#</span>
        <span>Object Type</span>
        <span>Description</span>
        <span>Available</span>
        <span>Object Count</span>
        <span>Status</span>
      </div>
      <div className="resource-list">
        {items.length === 0 ? (
          <p className="resource-empty">No catalog items in this category.</p>
        ) : (
          items.map((item, index) => (
            <label className="resource-item" key={item.id}>
              <span className="resource-index">{index + 1}</span>
              <span className="resource-item-copy">
                <strong>{item.name}</strong>
                <small>{item.status}</small>
              </span>
              <small className="resource-desc">{item.description}</small>
              <input
                aria-label={`${item.name} available`}
                checked={item.available}
                disabled={updatingId === item.id}
                onChange={(event) => onChange(item, event.target.checked)}
                type="checkbox"
              />
              <small className="resource-count">{item.count ?? 0} objects</small>
              <span className={`resource-status ${item.available ? "available" : "missing"}`}>
                {item.available ? <CheckIcon size={10} /> : <WarningIcon size={10} />}
                {item.available ? "Available" : "Not available"}
              </span>
            </label>
          ))
        )}
      </div>
    </section>
  );
}

export function ResourcesPanel({
  confirmed,
  refreshKey,
}: {
  confirmed: boolean;
  refreshKey: number;
}) {
  const [items, setItems] = useState<ResourceItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [updatingId, setUpdatingId] = useState<string | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!confirmed) {
      setItems([]);
      return;
    }
    let active = true;
    setLoading(true);
    setError("");
    void getResources()
      .then((nextItems) => {
        if (active) {
          setItems(nextItems);
        }
      })
      .catch((loadError: unknown) => {
        if (active) {
          setError(loadError instanceof Error ? loadError.message : "Could not load resources.");
        }
      })
      .finally(() => {
        if (active) {
          setLoading(false);
        }
      });
    return () => {
      active = false;
    };
  }, [confirmed, refreshKey]);

  async function changeAvailability(item: ResourceItem, available: boolean) {
    setUpdatingId(item.id);
    setError("");
    setItems((current) =>
      current.map((resource) =>
        resource.id === item.id ? { ...resource, available } : resource,
      ),
    );
    try {
      await updateResourceAvailability(item.id, available);
    } catch (updateError) {
      setItems((current) =>
        current.map((resource) =>
          resource.id === item.id ? { ...resource, available: item.available } : resource,
        ),
      );
      setError(updateError instanceof Error ? updateError.message : "Could not update resource.");
    } finally {
      setUpdatingId(null);
    }
  }

  if (!confirmed) {
    return (
      <div className="resource-empty-state">
        Confirm a source and target to load the source catalog.
      </div>
    );
  }

  return (
    <div className="resource-checklist">
      {error && <p className="resource-error" role="alert">{error}</p>}
      {loading ? (
        <p className="resource-empty-state">Loading source catalog…</p>
      ) : (
        <div className="resource-panes">
          <ResourceGroup
            items={items.filter((item) => item.tier === "mandatory")}
            onChange={(item, available) => void changeAvailability(item, available)}
            subtitle="Required for migration"
            title="Required"
            updatingId={updatingId}
          />
          <ResourceGroup
            items={items.filter((item) => item.tier === "optional")}
            onChange={(item, available) => void changeAvailability(item, available)}
            subtitle="Recommended"
            title="Recommended"
            updatingId={updatingId}
          />
        </div>
      )}
    </div>
  );
}
