import pytest

from server.app.security import validate_download_url, validate_package_filename


class TestDownloadUrlSingleImplementation:
    def test_all_entry_points_share_one_implementation(self):
        """#200：server / 节点 SDK 两个入口必须解析到同一个实现对象——
        防止有人把某一侧改回本地副本（sync-by-comment 时代的回归）。"""
        from workspace_libs.download import validate_download_url as sdk_side
        from workspace_libs.url_guard import validate_download_url as canonical

        assert validate_download_url is canonical
        assert sdk_side is canonical


class TestValidateDownloadUrl:
    def test_allows_https_public_url(self):
        validate_download_url("https://example.com/video.mp4")

    def test_allows_http_public_url(self):
        validate_download_url("http://example.com/video.mp4")

    def test_rejects_localhost(self):
        with pytest.raises(ValueError, match="Invalid URL"):
            validate_download_url("http://localhost/admin")

    def test_rejects_loopback_ip(self):
        with pytest.raises(ValueError, match="Invalid URL"):
            validate_download_url("http://127.0.0.1/secrets")

    def test_rejects_127_subnet(self):
        with pytest.raises(ValueError, match="Invalid URL"):
            validate_download_url("http://127.0.0.53/dns")

    def test_rejects_10_subnet(self):
        with pytest.raises(ValueError, match="Invalid URL"):
            validate_download_url("http://10.0.0.1/metadata")

    def test_rejects_172_16_subnet(self):
        with pytest.raises(ValueError, match="Invalid URL"):
            validate_download_url("http://172.16.0.1/metadata")

    def test_rejects_172_31_subnet(self):
        with pytest.raises(ValueError, match="Invalid URL"):
            validate_download_url("http://172.31.255.255/metadata")

    def test_rejects_192_168_subnet(self):
        with pytest.raises(ValueError, match="Invalid URL"):
            validate_download_url("http://192.168.1.1/router")

    def test_rejects_link_local_169_254(self):
        with pytest.raises(ValueError, match="Invalid URL"):
            validate_download_url("http://169.254.169.254/latest/meta-data/")

    def test_rejects_zero_ip(self):
        with pytest.raises(ValueError, match="Invalid URL"):
            validate_download_url("http://0.0.0.0/")

    def test_rejects_ipv6_loopback(self):
        with pytest.raises(ValueError, match="Invalid URL"):
            validate_download_url("http://[::1]/")

    def test_rejects_file_protocol(self):
        with pytest.raises(ValueError, match="Invalid URL"):
            validate_download_url("file:///etc/passwd")

    def test_rejects_ftp_protocol(self):
        with pytest.raises(ValueError, match="Invalid URL"):
            validate_download_url("ftp://example.com/video.mp4")

    def test_rejects_no_scheme(self):
        with pytest.raises(ValueError, match="Invalid URL"):
            validate_download_url("example.com/video.mp4")

    def test_rejects_empty(self):
        with pytest.raises(ValueError, match="Invalid URL"):
            validate_download_url("")

    def test_rejects_octal_ip(self):
        with pytest.raises(ValueError, match="Invalid URL"):
            validate_download_url("http://0177.0.0.1/secrets")

    def test_rejects_hex_ip(self):
        with pytest.raises(ValueError, match="Invalid URL"):
            validate_download_url("http://0x7f.0.0.1/secrets")


class TestValidatePackageFilename:
    def test_allows_simple_filename(self):
        assert validate_package_filename("package.zip") == "package.zip"

    def test_allows_filename_with_dot(self):
        assert validate_package_filename("my.package.v1.zip") == "my.package.v1.zip"

    def test_rejects_dotdot(self):
        with pytest.raises(ValueError, match="Invalid filename"):
            validate_package_filename("../etc/passwd")

    def test_rejects_forward_slash(self):
        with pytest.raises(ValueError, match="Invalid filename"):
            validate_package_filename("foo/bar.zip")

    def test_rejects_backslash(self):
        with pytest.raises(ValueError, match="Invalid filename"):
            validate_package_filename("foo\\bar.zip")

    def test_rejects_empty(self):
        with pytest.raises(ValueError, match="Invalid filename"):
            validate_package_filename("")

    def test_rejects_leading_dot(self):
        with pytest.raises(ValueError, match="Invalid filename"):
            validate_package_filename(".hidden.zip")

    def test_rejects_leading_hyphen(self):
        with pytest.raises(ValueError, match="Invalid filename"):
            validate_package_filename("-dangerous.zip")
