import { fetchAuthSession } from 'aws-amplify/auth';
import { config } from './config';
import type { BriefListItem, BriefDetail, CreateBriefResponse } from './types';

const BASE = config.apiBase;

async function authHeaders() {
  try {
    const session = await fetchAuthSession();
    const idToken = session.tokens?.idToken?.toString();
    console.log('Auth session:', { hasSession: !!session, hasTokens: !!session.tokens, hasIdToken: !!idToken });
    return idToken ? { Authorization: `Bearer ${idToken}` } : {};
  } catch (error) {
    console.warn('Failed to get auth token:', error);
    return {};
  }
}

export async function createBrief(candidate: string, jobTitle: string): Promise<CreateBriefResponse> {
  const headers = await authHeaders();
  console.log('createBrief request:', { 
    url: `${BASE}/briefs`, 
    headers: { 'Content-Type': 'application/json', ...headers },
    body: { candidate: { fullName: candidate }, job: { title: jobTitle } }
  });
  const res = await fetch(`${BASE}/briefs`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...headers },
    body: JSON.stringify({ 
      candidate: { fullName: candidate }, 
      job: { title: jobTitle } 
    }),
  });
  console.log('createBrief response:', { status: res.status, statusText: res.statusText });
  if (!res.ok) throw new Error(`createBrief failed: ${res.status} ${res.statusText}`);
  return res.json();
}

export async function startBrief(id: string): Promise<{ message: string }> {
  const headers = await authHeaders();
  const res = await fetch(`${BASE}/briefs/${id}/start`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', ...headers },
  });
  if (!res.ok) throw new Error(`startBrief failed: ${res.status} ${res.statusText}`);
  return res.json();
}

export async function listBriefs(): Promise<BriefListItem[]> {
  const headers = await authHeaders();
  const res = await fetch(`${BASE}/briefs`, { headers });
  if (!res.ok) throw new Error(`listBriefs failed: ${res.status} ${res.statusText}`);
  return res.json();
}

export async function getBrief(id: string): Promise<BriefDetail> {
  const headers = await authHeaders();
  const res = await fetch(`${BASE}/briefs/${id}`, { headers });
  if (!res.ok) throw new Error(`getBrief failed: ${res.status} ${res.statusText}`);
  return res.json();
}

/** NEW: delete a brief (backend enforces ownership and status guard) */
export async function deleteBrief(id: string): Promise<{ message: string }> {
  if (!BASE) throw new Error('API base URL not configured (config.apiBase).');
  const headers = await authHeaders();
  const res = await fetch(`${BASE}/briefs/${id}`, {
    method: 'DELETE',
    headers: { 'Content-Type': 'application/json', ...headers },
  });

  // Try to parse JSON error/success bodies; fall back to text
  const text = await res.text();
  let data: any = undefined;
  try { data = text ? JSON.parse(text) : undefined; } catch { /* noop */ }

  if (!res.ok) {
    const msg = data?.message || text || `deleteBrief failed: ${res.status} ${res.statusText}`;
    throw new Error(msg);
  }
  return (data ?? { message: 'deleted' }) as { message: string };
}
