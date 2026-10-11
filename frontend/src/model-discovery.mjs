const PUBLIC_KEYLESS = new Set(["openrouter"]);

export function createModelDiscovery(fetcher) {
  let generation = 0;
  let controller = null;
  return {
    async fetch(input) {
      controller?.abort();
      const current = ++generation;
      if (!input?.provider || !input?.base_url || (!input.api_key && !PUBLIC_KEYLESS.has(input.provider))) return null;
      if (input.api_key && (input.api_key.length < 8 || input.api_key.length > 2048)) throw new Error("model_discovery_invalid_key");
      const next = new AbortController();
      controller = next;
      try {
        const payload = {provider: input.provider, base_url: input.base_url};
        if (input.api_key?.trim()) payload.api_key = input.api_key;
        const result = await fetcher(payload, {signal: next.signal});
        if (current !== generation || next.signal.aborted) return null;
        return {...result, models: Array.isArray(result?.models) ? result.models.slice(0, 5) : []};
      } catch (error) {
        if (current !== generation || next.signal.aborted) return null;
        throw error;
      }
    },
    cancel() {
      generation++;
      controller?.abort();
      controller = null;
    },
  };
}

export function createModelDiscoveryScheduler(schedule = setTimeout, cancel = clearTimeout) {
  let timer = null;
  let context = null;
  return {
    setContext(nextContext) {
      if (context !== nextContext) {
        if (timer !== null) cancel(timer);
        timer = null;
        context = nextContext;
      }
    },
    schedule(callback, delay = 750) {
      if (timer !== null) cancel(timer);
      timer = schedule(() => { timer = null; callback(); }, delay);
    },
    cancel() {
      if (timer !== null) cancel(timer);
      timer = null;
    },
  };
}
