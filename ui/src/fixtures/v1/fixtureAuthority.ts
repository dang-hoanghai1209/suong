type JsonPrimitive = string | number | boolean | null;
type JsonValue = JsonPrimitive | JsonValue[] | { [key: string]: JsonValue };

function assertJsonSafe(value: unknown, path: string, ancestors: Set<object>): void {
  if (
    value === null ||
    typeof value === "string" ||
    typeof value === "boolean"
  ) {
    return;
  }
  if (typeof value === "number") {
    if (!Number.isFinite(value)) {
      throw new TypeError(`${path} must contain only finite JSON numbers`);
    }
    return;
  }
  if (typeof value !== "object") {
    throw new TypeError(`${path} contains a non-JSON value`);
  }
  if (ancestors.has(value)) {
    throw new TypeError(`${path} contains a circular reference`);
  }

  ancestors.add(value);
  if (Array.isArray(value)) {
    const allowedKeys = new Set(["length"]);
    for (let index = 0; index < value.length; index += 1) {
      const key = String(index);
      allowedKeys.add(key);
      if (!Object.hasOwn(value, key)) {
        throw new TypeError(`${path} contains a sparse non-JSON array`);
      }
      assertJsonSafe(value[index], `${path}[${index}]`, ancestors);
    }
    if (Reflect.ownKeys(value).some((key) => !allowedKeys.has(String(key)))) {
      throw new TypeError(`${path} contains a non-JSON array property`);
    }
  } else {
    const prototype = Object.getPrototypeOf(value);
    if (prototype !== Object.prototype && prototype !== null) {
      throw new TypeError(`${path} contains a non-JSON object`);
    }
    for (const key of Reflect.ownKeys(value)) {
      if (typeof key !== "string") {
        throw new TypeError(`${path} contains a non-JSON property key`);
      }
      const descriptor = Object.getOwnPropertyDescriptor(value, key);
      if (
        descriptor === undefined ||
        !descriptor.enumerable ||
        !Object.hasOwn(descriptor, "value")
      ) {
        throw new TypeError(`${path}.${key} is not a plain JSON property`);
      }
      assertJsonSafe(descriptor.value, `${path}.${key}`, ancestors);
    }
  }
  ancestors.delete(value);
}

function deepFreeze<T>(value: T): T {
  if (value !== null && typeof value === "object" && !Object.isFrozen(value)) {
    for (const child of Object.values(value)) {
      deepFreeze(child);
    }
    Object.freeze(value);
  }
  return value;
}

export function createCanonicalJsonFixture<T>(value: T): Readonly<T> {
  assertJsonSafe(value, "$", new Set());
  return deepFreeze(structuredClone(value));
}

export function detachCanonicalJsonFixture<T>(value: Readonly<T>): T {
  assertJsonSafe(value, "$", new Set());
  return structuredClone(value);
}

export type { JsonValue };
