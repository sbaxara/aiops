// Copyright (c) 2026 Axara Pty Ltd.
// SPDX-License-Identifier: AGPL-3.0-or-later
import { useState, useEffect } from 'react';

export const DEFAULT_PRODUCT_NAME = 'Axara AIOps';

// Release version, set at image build time from AXARA_VERSION (see dashboard/Dockerfile).
// eslint-disable-next-line no-undef
export const APP_VERSION = typeof __APP_VERSION__ !== 'undefined' ? __APP_VERSION__ : 'dev';

// Product name from the branding settings, or the default name.
export function productName(branding) {
  return (branding && branding.product_name) || DEFAULT_PRODUCT_NAME;
}

// Branding hook
// Loads /branding/branding.json at runtime.  Customers drop files into the
// ./branding host directory (mounted read-only into the nginx container).
// All keys are optional — the UI falls back to built-in defaults gracefully.
//
// branding.json schema:
//   {
//     "product_name": "Acme Monitor",        // shown on sign-in, sidebar and browser tab
//     "company_name": "Acme Corp",
//     "copyright": "© 2026 Acme Corp. All rights reserved.",
//     "logo_url": "/branding/logo.png",     // or any URL
//     "favicon_url": "/branding/favicon.ico",
//     "css_url": "/branding/custom.css"     // loaded last — overrides defaults
//   }
export function useBranding() {
  const [branding, setBranding] = useState({});

  useEffect(() => {
    fetch('/branding/branding.json', { cache: 'no-store' })
      .then(r => r.ok ? r.json() : {})
      .then(data => setBranding(data ?? {}))
      .catch(() => {});  // branding dir absent — silently use defaults
  }, []);

  // Inject custom CSS override <link> into <head> once url is known
  useEffect(() => {
    if (!branding.css_url) return;
    const id = 'branding-css-override';
    if (document.getElementById(id)) return;  // already injected
    const link = document.createElement('link');
    link.id   = id;
    link.rel  = 'stylesheet';
    link.href = branding.css_url;
    document.head.appendChild(link);
  }, [branding.css_url]);

  // Update favicon
  useEffect(() => {
    if (!branding.favicon_url) return;
    let link = document.querySelector('link[rel~="icon"]');
    if (!link) {
      link = document.createElement('link');
      link.rel = 'icon';
      document.head.appendChild(link);
    }
    link.href = branding.favicon_url;
  }, [branding.favicon_url]);

  // Browser tab title follows the product name
  useEffect(() => {
    document.title = productName(branding);
  }, [branding.product_name, branding.company_name]);

  return branding;
}
