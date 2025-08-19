export async function putToPresigned(url: string, blob: Blob, contentType: string): Promise<void> {
  const res = await fetch(url, {
    method: 'PUT',
    headers: { 'Content-Type': contentType },
    body: blob,
  });
  if (!res.ok) throw new Error(`Upload failed: ${res.status} ${res.statusText}`);
}