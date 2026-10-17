package com.daythread.app.core;

import java.net.URI;
import java.util.List;

/** A capture owns this snapshot; editing the connection cannot retarget pending uploads. */
public record Destination(List<String> endpoints, String profile) {
    public Destination {
        endpoints = endpoints.stream().map(Destination::normalize).distinct().collect(java.util.stream.Collectors.toList());
        if (endpoints.isEmpty() || endpoints.size() > 8) throw new IllegalArgumentException("Choose 1–8 backend endpoints");
        if (profile == null || !profile.matches("[a-zA-Z0-9_-]{1,80}")) throw new IllegalArgumentException("Choose a profile");
    }
    public static String normalize(String raw) {
        URI uri = URI.create(raw.trim());
        if (!("https".equals(uri.getScheme()) || "http".equals(uri.getScheme())) || uri.getHost() == null
                || uri.getRawUserInfo() != null || uri.getRawQuery() != null || uri.getRawFragment() != null
                || !(uri.getPath().isEmpty() || uri.getPath().equals("/")) || uri.getPort() == 0 || uri.getPort() > 65535)
            throw new IllegalArgumentException("Use a backend origin such as https://daythread.home.arpa");
        return uri.getScheme() + "://" + uri.getRawAuthority();
    }
    public static boolean sameOrigin(String endpoint, String url) {
        try {
            URI a = URI.create(endpoint), b = URI.create(url);
            int ap = a.getPort() < 0 ? (a.getScheme().equals("https") ? 443 : 80) : a.getPort();
            int bp = b.getPort() < 0 ? ("https".equals(b.getScheme()) ? 443 : 80) : b.getPort();
            return a.getScheme().equals(b.getScheme()) && a.getHost().equalsIgnoreCase(b.getHost()) && ap == bp && b.getUserInfo() == null;
        } catch (RuntimeException e) { return false; }
    }
}
