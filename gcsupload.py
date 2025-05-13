from google.cloud import storage
import os

def upload_to_gcs(bucket_name, source_file_path, destination_blob_name, credentials_path=None):
    """
    上传本地文件到指定的 GCS 存储桶。

    :param bucket_name: GCS 存储桶名称
    :param source_file_path: 本地文件路径
    :param destination_blob_name: 上传到 GCS 后的文件名称
    :param credentials_path: （可选）服务账号密钥文件的路径
    :return: 上传文件的公共 URL
    """
    # 创建存储客户端
    if credentials_path:
        storage_client = storage.Client.from_service_account_json(credentials_path)
    else:
        storage_client = storage.Client()

    try:
        # 获取存储桶
        bucket = storage_client.bucket(bucket_name)
        # 创建 blob 对象
        blob = bucket.blob(destination_blob_name)
        # 上传文件
        blob.upload_from_filename(source_file_path)
        print(f"文件 {source_file_path} 已成功上传到 {bucket_name}/{destination_blob_name}。")
        return blob.public_url
    except Exception as e:
        print(f"上传过程中发生错误: {e}")
        return None


if __name__ == "__main__":
    bucket_name = "cdn.jiwu.com.au"
    source_file_path = "/home/sp/qq.txt"
    destination_blob_name = "liw-test.txt"
    credentials_path = "/home/sp/res_listing/jiwu.json"  # 如果已设置环境变量，可将此项设为 None

    public_url = upload_to_gcs(bucket_name, source_file_path, destination_blob_name, credentials_path)
    if public_url:
        print(f"文件的公共 URL: {public_url}")
