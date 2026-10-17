package com.daythread.app.core;

import org.json.JSONObject;
import org.json.JSONArray;
import org.json.JSONException;
import java.util.Collection;

/** Strict JSON access shared by Android and JVM tests (Android's JSONException is checked). */
public final class Json {
    private final JSONObject value;
    public Json() { value = new JSONObject(); }
    public Json(String text) {
        try { value = new JSONObject(text); } catch (JSONException e) { throw new IllegalArgumentException("Invalid JSON", e); }
    }
    private Json(JSONObject value) { this.value = value; }
    public Json put(String key, Object item) {
        try {
            value.put(key, item instanceof Json json ? json.value : item instanceof JsonArray array ? array.value : item);
            return this;
        } catch (JSONException e) { throw new IllegalArgumentException("Invalid JSON value", e); }
    }
    public Object get(String key) {
        Object item = value.opt(key);
        if (item == null || item == JSONObject.NULL) throw new IllegalArgumentException("Missing " + key);
        return item;
    }
    public String getString(String key) {
        if (get(key) instanceof String item) return item;
        throw new IllegalArgumentException("Invalid " + key);
    }
    public int getInt(String key) {
        if (get(key) instanceof Number item) return item.intValue();
        throw new IllegalArgumentException("Invalid " + key);
    }
    public Json getJson(String key) {
        if (get(key) instanceof JSONObject item) return new Json(item);
        throw new IllegalArgumentException("Invalid " + key);
    }
    public JsonArray getJsonArray(String key) {
        if (get(key) instanceof JSONArray item) return new JsonArray(item);
        throw new IllegalArgumentException("Invalid " + key);
    }
    public String optString(String key) { return optString(key, ""); }
    public String optString(String key, String fallback) { return value.opt(key) instanceof String item ? item : fallback; }
    public boolean has(String key) { return value.has(key); }
    @Override public String toString() { return value.toString(); }

    public static final class JsonArray {
        private final JSONArray value;
        public JsonArray(Collection<?> items) { value = new JSONArray(items); }
        private JsonArray(JSONArray value) { this.value = value; }
        public int length() { return value.length(); }
        public String getString(int index) {
            if (value.opt(index) instanceof String item) return item;
            throw new IllegalArgumentException("Invalid array entry");
        }
        public Json getJson(int index) {
            if (value.opt(index) instanceof JSONObject item) return new Json(item);
            throw new IllegalArgumentException("Invalid array entry");
        }
    }
}
