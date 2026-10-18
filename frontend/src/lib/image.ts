/** Screenshots are compressed on the phone before upload: long side ≤ 1920 px, JPEG. */
const MAX_BYTES = 10 * 1024 * 1024;

export async function compressImage(file: File, maxSide = 1920, quality = 0.85): Promise<Blob> {
  if (!file.type.startsWith("image/")) throw new Error("Нужна картинка: скриншот или фото");
  let bitmap: ImageBitmap;
  try {
    bitmap = await createImageBitmap(file);
  } catch {
    // the browser can't decode it (rare formats): let the server check the original
    if (file.size > MAX_BYTES) throw new Error("Файл больше 10 МБ");
    return file;
  }
  const scale = Math.min(1, maxSide / Math.max(bitmap.width, bitmap.height));
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(bitmap.width * scale);
  canvas.height = Math.round(bitmap.height * scale);
  canvas.getContext("2d")!.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
  bitmap.close();
  const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, "image/jpeg", quality));
  if (!blob) return file;
  if (blob.size > MAX_BYTES) throw new Error("Файл больше 10 МБ");
  return blob;
}
