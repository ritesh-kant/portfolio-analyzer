export function timeoutPromise(ms: number): Promise<never> {
  return new Promise((_, reject) =>
    setTimeout(() => reject(new Error(`LLM call timed out after ${ms}ms`)), ms),
  );
}
