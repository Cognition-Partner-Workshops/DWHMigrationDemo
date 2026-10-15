import { useEffect, useState } from "react";

import { getResources, updateResourceAvailability, type ResourceItem } from "./api";

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
  return (
    <section className={`resource-category ${title === "Required" ? "required" : ""}`}>
      <header>
        <strong>{title}</strong>
        <span>{subtitle}</span>
      </header>
      <div className="resource-list">
        {items.length === 0 ? (
          <p className="resource-empty">No catalog items in this category.</p>
        ) : (
          items.map((item) => (
            <label className="resource-item" key={item.id}>
              <span className="resource-item-copy">
                <strong>{item.name}</strong>
                <small>{item.description}</small>
                <span>{item.status}</span>
                <small className="resource-count">{item.count ?? 0} objects</small>
              </span>
              <input
                aria-label={`${item.name} available`}
                checked={item.available}
                disabled={updatingId === item.id}
                onChange={(event) => onChange(item, event.target.checked)}
                type="checkbox"
              />
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
