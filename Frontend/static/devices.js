"use strict";
(() => {
  // frontend/format.ts
  var dateTime = new Intl.DateTimeFormat(void 0, { dateStyle: "medium", timeStyle: "short" });
  function formatTime(iso, fallback = "Not recorded") {
    if (!iso) return fallback;
    const value = new Date(iso);
    return Number.isNaN(value.getTime()) ? fallback : dateTime.format(value);
  }
  function formatAgo(iso, fallback = "never") {
    if (!iso) return fallback;
    const value = new Date(iso);
    if (Number.isNaN(value.getTime())) return fallback;
    const seconds = Math.max(0, (Date.now() - value.getTime()) / 1e3);
    if (seconds < 60) return "just now";
    if (seconds < 3600) return `${Math.round(seconds / 60)} min ago`;
    if (seconds < 86400) return `${Math.round(seconds / 3600)} h ago`;
    return formatTime(iso);
  }

  // frontend/devices.ts
  var $ = (selector) => document.querySelector(selector);
  function responseError(payload, status) {
    if (payload && typeof payload === "object" && "error" in payload && typeof payload.error === "string") {
      return payload.error;
    }
    return `Request failed (${status}).`;
  }
  function setStatus(message, isError = false) {
    const status = $("#deviceListStatus");
    if (!status) return;
    status.textContent = message;
    status.classList.toggle("error", isError);
  }
  var deviceName = (device) => device.name && device.name.length > 0 ? device.name : "Unnamed device";
  function renderDevices(devices) {
    const list = $("#deviceList");
    if (!list) return;
    list.replaceChildren();
    setStatus(
      devices.length === 0 ? "No paired devices. Pair one from Setup." : `${devices.length} paired device${devices.length === 1 ? "" : "s"}.`
    );
    for (const device of devices) {
      const item = document.createElement("li");
      item.className = "alert-item";
      const title = document.createElement("strong");
      title.textContent = deviceName(device);
      const body = document.createElement("p");
      const seen = device.last_seen ? `Last check-in ${formatAgo(device.last_seen)}.` : "No check-in has been recorded.";
      const presence = device.revoked ? "Revoked. The agent's connection no longer works." : device.stale ? "Offline." : "Connected.";
      body.textContent = `${seen} ${presence}`;
      if (device.last_seen) body.title = formatTime(device.last_seen);
      item.append(title, body);
      if (!device.revoked) {
        const actions = document.createElement("div");
        actions.className = "alert-actions";
        const button = document.createElement("button");
        button.type = "button";
        button.className = "button secondary-button";
        button.textContent = "Revoke";
        button.addEventListener("click", () => {
          const confirmed = window.confirm(
            `Revoke ${deviceName(device)}? Its agent stops sending data and needs a new pairing code to reconnect.`
          );
          if (!confirmed) return;
          button.disabled = true;
          void revokeDevice(device, button);
        });
        actions.append(button);
        item.append(actions);
      }
      list.append(item);
    }
  }
  async function revokeDevice(device, button) {
    try {
      const response = await fetch(`/api/devices/${encodeURIComponent(device.id)}/revoke`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}"
      });
      if (!response.ok) throw new Error(responseError(await response.json().catch(() => null), response.status));
      await refreshDevices();
      setStatus(`${deviceName(device)} was revoked.`);
    } catch (error) {
      button.disabled = false;
      setStatus(
        error instanceof TypeError ? "Could not revoke that device. The server did not respond." : error instanceof Error ? error.message : "Could not revoke that device.",
        true
      );
    }
  }
  async function refreshDevices() {
    let response;
    try {
      response = await fetch("/api/devices");
    } catch {
      setStatus("Could not load devices. The server did not respond.", true);
      return;
    }
    if (response.status === 404) {
      setStatus("Device list is not available on this server.");
      return;
    }
    if (!response.ok) {
      setStatus(`Could not load devices (${response.status}).`, true);
      return;
    }
    renderDevices((await response.json()).devices ?? []);
  }
  void refreshDevices();
})();
