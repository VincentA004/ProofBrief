/**
 * Uploads a file to a pre-signed S3 URL.
 * @param url The pre-signed URL from the backend.
 * @param file The file object to upload.
 * @returns {Promise<Response>} The response from the fetch call.
 */
export async function uploadFile(url: string, file: File): Promise<Response> {
  const res = await fetch(url, {
    method: 'PUT',
    headers: {
      'Content-Type': file.type,
    },
    body: file,
  });

  if (!res.ok) {
    throw new Error(`File upload failed: ${res.status} ${res.statusText}`);
  }

  return res;
}