/* Advanced Automations v1.0.7 */

class ApiError extends Error {
  constructor(message, status = 0, details = []) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.details = Array.isArray(details) ? details : [];
  }
}

async function api(path, options = {}) {
  const { returnResponse = false, ...fetchOptions } = options;
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(fetchOptions.headers || {}) },
    ...fetchOptions,
  });
  let data = null;
  if (response.status !== 204) {
    const contentType = response.headers.get("content-type") || "";
    try {
      data = contentType.includes("application/json") ? await response.json() : { error: await response.text() };
    } catch (_) {
      data = null;
    }
  }
  if (!response.ok) {
    throw new ApiError(data?.error || `Request failed (${response.status})`, response.status, data?.details || []);
  }
  return returnResponse ? { data, response } : data;
}

