"""Test The generic (IP Camera) config flow."""

from unittest.mock import patch

import av
import pytest
import respx

from homeassistant import config_entries, data_entry_flow, setup
import homeassistant.components.generic
from homeassistant.components.generic.const import (
    CONF_CONTENT_TYPE,
    CONF_FRAMERATE,
    CONF_LIMIT_REFETCH_TO_URL_CHANGE,
    CONF_RTSP_TRANSPORT,
    CONF_STILL_IMAGE_URL,
    CONF_STREAM_SOURCE,
    DOMAIN,
)
from homeassistant.const import (
    CONF_AUTHENTICATION,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_USERNAME,
    CONF_VERIFY_SSL,
    HTTP_BASIC_AUTHENTICATION,
)

from tests.common import MockConfigEntry

TESTDATA = {
    CONF_NAME: "cam1",
    CONF_STILL_IMAGE_URL: "http://127.0.0.1/testurl/1",
    CONF_STREAM_SOURCE: "http://127.0.0.2/testurl/2",
    CONF_AUTHENTICATION: HTTP_BASIC_AUTHENTICATION,
    CONF_USERNAME: "fred_flintstone",
    CONF_PASSWORD: "bambam",
    CONF_LIMIT_REFETCH_TO_URL_CHANGE: False,
    CONF_CONTENT_TYPE: "image/jpeg",
    CONF_FRAMERATE: 5,
    CONF_VERIFY_SSL: False,
}


@respx.mock
async def test_form(hass, fakeimgbytes_png, fakevidcontainer):
    """Test the form with a normal set of settings."""
    respx.get("http://127.0.0.1/testurl/1").respond(stream=fakeimgbytes_png)
    await setup.async_setup_component(hass, "persistent_notification", {})
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] == "form"
    assert result["errors"] == {}

    with patch("av.open", return_value=fakevidcontainer) as mock_setup:
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            TESTDATA,
        )
    assert result2["type"] == data_entry_flow.RESULT_TYPE_CREATE_ENTRY
    assert result2["title"] == "cam1"
    assert result2["data"] == {
        CONF_NAME: "cam1",
        CONF_STILL_IMAGE_URL: "http://127.0.0.1/testurl/1",
        CONF_STREAM_SOURCE: "http://127.0.0.2/testurl/2",
        CONF_RTSP_TRANSPORT: None,
        CONF_AUTHENTICATION: HTTP_BASIC_AUTHENTICATION,
        CONF_USERNAME: "fred_flintstone",
        CONF_PASSWORD: "bambam",
        CONF_LIMIT_REFETCH_TO_URL_CHANGE: False,
        CONF_CONTENT_TYPE: "image/jpeg",
        CONF_FRAMERATE: 5,
        CONF_VERIFY_SSL: False,
    }

    await hass.async_block_till_done()
    assert len(mock_setup.mock_calls) == 1


@respx.mock
async def test_form_only_stillimage(hass, fakeimgbytes_png, fakevidcontainer):
    """Test we complete ok if the user wants still images only."""
    respx.get("http://127.0.0.1/testurl/1").respond(stream=fakeimgbytes_png)
    await setup.async_setup_component(hass, "persistent_notification", {})
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] == "form"
    assert result["errors"] == {}

    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "cam1",
            CONF_STILL_IMAGE_URL: "http://127.0.0.1/testurl/1",
            CONF_AUTHENTICATION: HTTP_BASIC_AUTHENTICATION,
            CONF_USERNAME: "fred_flintstone",
            CONF_PASSWORD: "bambam",
            CONF_LIMIT_REFETCH_TO_URL_CHANGE: False,
            CONF_CONTENT_TYPE: "image/jpeg",
            CONF_FRAMERATE: 5,
            CONF_VERIFY_SSL: False,
        },
    )
    assert result2["type"] == data_entry_flow.RESULT_TYPE_CREATE_ENTRY
    assert result2["title"] == "cam1"
    assert result2["data"] == {
        CONF_NAME: "cam1",
        CONF_STILL_IMAGE_URL: "http://127.0.0.1/testurl/1",
        CONF_AUTHENTICATION: HTTP_BASIC_AUTHENTICATION,
        CONF_RTSP_TRANSPORT: None,
        CONF_USERNAME: "fred_flintstone",
        CONF_PASSWORD: "bambam",
        CONF_LIMIT_REFETCH_TO_URL_CHANGE: False,
        CONF_CONTENT_TYPE: "image/jpeg",
        CONF_FRAMERATE: 5,
        CONF_VERIFY_SSL: False,
    }

    await hass.async_block_till_done()
    assert respx.calls.call_count == 1


@respx.mock
async def test_form_rtsp_mode(hass, fakeimgbytes_png, fakevidcontainer):
    """Test we complete ok if the user enters a stream url."""
    respx.get("http://127.0.0.1/testurl/1").respond(stream=fakeimgbytes_png)
    await setup.async_setup_component(hass, "persistent_notification", {})
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] == "form"
    assert result["errors"] == {}

    with patch("av.open", return_value=fakevidcontainer) as mock_setup:
        data = TESTDATA
        data[CONF_RTSP_TRANSPORT] = "tcp"
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"], data
        )
    print(f"result2={result2}")
    assert "errors" not in result2, f"errors={result2['errors']}"
    assert result2["type"] == data_entry_flow.RESULT_TYPE_CREATE_ENTRY
    assert result2["title"] == "cam1"
    assert result2["data"] == {
        CONF_NAME: "cam1",
        CONF_STILL_IMAGE_URL: "http://127.0.0.1/testurl/1",
        CONF_AUTHENTICATION: HTTP_BASIC_AUTHENTICATION,
        CONF_STREAM_SOURCE: "http://127.0.0.2/testurl/2",
        CONF_RTSP_TRANSPORT: "tcp",
        CONF_USERNAME: "fred_flintstone",
        CONF_PASSWORD: "bambam",
        CONF_LIMIT_REFETCH_TO_URL_CHANGE: False,
        CONF_CONTENT_TYPE: "image/jpeg",
        CONF_FRAMERATE: 5,
        CONF_VERIFY_SSL: False,
    }

    await hass.async_block_till_done()
    assert len(mock_setup.mock_calls) == 1


async def test_form_only_stream(hass, fakevidcontainer):
    """Test we complete ok if the user wants stream only."""
    await setup.async_setup_component(hass, "persistent_notification", {})
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    with patch("av.open", return_value=fakevidcontainer) as mock_setup:
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_NAME: "cam1",
                CONF_AUTHENTICATION: HTTP_BASIC_AUTHENTICATION,
                CONF_STREAM_SOURCE: "http://127.0.0.2/testurl/2",
                CONF_RTSP_TRANSPORT: "tcp",
                CONF_USERNAME: "fred_flintstone",
                CONF_PASSWORD: "bambam",
                CONF_LIMIT_REFETCH_TO_URL_CHANGE: False,
                CONF_CONTENT_TYPE: "image/jpeg",
                CONF_FRAMERATE: 5,
                CONF_VERIFY_SSL: False,
            },
        )
    assert result2["type"] == data_entry_flow.RESULT_TYPE_CREATE_ENTRY
    assert result2["title"] == "cam1"
    assert result2["data"] == {
        CONF_NAME: "cam1",
        CONF_AUTHENTICATION: HTTP_BASIC_AUTHENTICATION,
        CONF_STREAM_SOURCE: "http://127.0.0.2/testurl/2",
        CONF_RTSP_TRANSPORT: "tcp",
        CONF_USERNAME: "fred_flintstone",
        CONF_PASSWORD: "bambam",
        CONF_LIMIT_REFETCH_TO_URL_CHANGE: False,
        CONF_CONTENT_TYPE: "image/jpeg",
        CONF_FRAMERATE: 5,
        CONF_VERIFY_SSL: False,
    }

    await hass.async_block_till_done()
    assert len(mock_setup.mock_calls) == 1


async def test_form_still_and_stream_not_provided(hass):
    """Test we show a suitable error if neither still or stream URL are provided."""
    await setup.async_setup_component(hass, "persistent_notification", {})
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "cam1",
            CONF_AUTHENTICATION: HTTP_BASIC_AUTHENTICATION,
            CONF_LIMIT_REFETCH_TO_URL_CHANGE: False,
            CONF_CONTENT_TYPE: "image/jpeg",
            CONF_FRAMERATE: 5,
            CONF_VERIFY_SSL: False,
        },
    )
    assert result2["type"] == data_entry_flow.RESULT_TYPE_FORM
    assert result2["errors"] == {"base": "no_still_image_or_stream_url"}


async def test_form_stream_noimage(hass, fakevidcontainer):
    """Test we handle image returning None when a stream is specified."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    with patch(
        "homeassistant.components.generic.camera.GenericCamera.async_camera_image",
        return_value=None,
    ), patch("av.open", return_value=fakevidcontainer):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            TESTDATA,
        )

    await hass.async_block_till_done()

    assert result2["type"] == "form"
    assert result2["errors"] == {"base": "unable_still_load"}


@respx.mock
async def test_form_stream_invalidimage(hass, fakevidcontainer):
    """Test we handle invalid image when a stream is specified."""
    respx.get("http://127.0.0.1/testurl/1").respond(stream=b"invalid")
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    test_data_new = TESTDATA.copy()
    test_data_new.pop(CONF_CONTENT_TYPE)  # don't specify format

    with patch("av.open", return_value=fakevidcontainer):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            test_data_new,
        )

    await hass.async_block_till_done()

    assert result2["type"] == "form"
    assert result2["errors"] == {"base": "invalid_still_image"}


@respx.mock
async def test_form_stream_file_not_found(hass, fakeimgbytes_png):
    """Test we handle file not found."""
    respx.get("http://127.0.0.1/testurl/1").respond(stream=fakeimgbytes_png)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    with patch("av.open", side_effect=av.error.FileNotFoundError(0, 0)):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            TESTDATA,
        )
    assert result2["type"] == "form"
    assert result2["errors"] == {"base": "stream_no_route_to_host"}


@respx.mock
async def test_form_stream_unauthorised(hass, fakeimgbytes_png):
    """Test we handle invalid auth."""
    respx.get("http://127.0.0.1/testurl/1").respond(stream=fakeimgbytes_png)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    with patch("av.open", side_effect=av.error.HTTPUnauthorizedError(0, 0)):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            TESTDATA,
        )
    assert result2["type"] == "form"
    assert result2["errors"] == {"base": "stream_unauthorised"}


@respx.mock
async def test_form_stream_novideo(hass, fakeimgbytes_png):
    """Test we handle invalid stream."""
    respx.get("http://127.0.0.1/testurl/1").respond(stream=fakeimgbytes_png)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    with patch("av.open", side_effect=KeyError()):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            TESTDATA,
        )
    assert result2["type"] == "form"
    assert result2["errors"] == {"base": "stream_novideo"}


@respx.mock
async def test_form_stream_permission_error(hass, fakeimgbytes_png):
    """Test we handle permission error."""
    respx.get("http://127.0.0.1/testurl/1").respond(stream=fakeimgbytes_png)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    with patch("av.open", side_effect=PermissionError()):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            TESTDATA,
        )
    assert result2["type"] == "form"
    assert result2["errors"] == {"base": "stream_not_permitted"}


@respx.mock
async def test_form_no_route_to_host(hass, fakeimgbytes_png):
    """Test we handle no route to host."""
    respx.get("http://127.0.0.1/testurl/1").respond(stream=fakeimgbytes_png)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    with patch("av.open", side_effect=OSError("No route to host")):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            TESTDATA,
        )
    assert result2["type"] == "form"
    assert result2["errors"] == {"base": "stream_no_route_to_host"}


@respx.mock
async def test_form_stream_io_error(hass, fakeimgbytes_png):
    """Test we handle no io error when setting up stream."""
    respx.get("http://127.0.0.1/testurl/1").respond(stream=fakeimgbytes_png)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    with patch("av.open", side_effect=OSError("Input/output error")):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            TESTDATA,
        )
    assert result2["type"] == "form"
    assert result2["errors"] == {"base": "stream_io_error"}


async def test_form_oserror(hass, fakeimgbytes_png):
    """Test we handle OS error when setting up stream."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    with pytest.raises(OSError), patch(
        "homeassistant.components.generic.camera.GenericCamera.async_camera_image",
        return_value=fakeimgbytes_png,
    ), patch("av.open", side_effect=OSError("Some other OSError")):
        await hass.config_entries.flow.async_configure(
            result["flow_id"],
            TESTDATA,
        )


@respx.mock
async def test_form_unique_id_already_in_use(hass, fakeimgbytes_png, fakevidcontainer):
    """Test we handle duplicate unique id."""
    respx.get("http://127.0.0.1/testurl/1").respond(stream=fakeimgbytes_png)
    await setup.async_setup_component(hass, "persistent_notification", {})
    flow1 = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert flow1["type"] == "form"
    assert flow1["errors"] == {}

    uniqueid = flow1["flow_id"]
    with patch("av.open", return_value=fakevidcontainer):
        result1 = await hass.config_entries.flow.async_configure(
            flow1["flow_id"],
            TESTDATA,
        )
    assert result1["type"] == data_entry_flow.RESULT_TYPE_CREATE_ENTRY

    with patch("homeassistant.util.uuid.random_uuid_hex", return_value=uniqueid):
        flow2 = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
    assert flow2["type"] == "form"
    assert flow2["errors"] == {}
    # second time round using same flow id (which is used as the unique id)
    # should result in an error
    with patch("av.open", return_value=fakevidcontainer):
        result2 = await hass.config_entries.flow.async_configure(
            flow2["flow_id"],
            TESTDATA,
        )
    assert result2["errors"] == {"base": "unique_id_already_in_use"}


# These below can be deleted after deprecation period is finished.
@respx.mock
async def test_import(hass, fakeimgbytes_png, fakevidcontainer):
    """Test configuration.yaml import used during migration."""
    respx.get("http://127.0.0.1/testurl/1").respond(stream=fakeimgbytes_png)
    with patch("av.open", return_value=fakevidcontainer):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_IMPORT}, data=TESTDATA
        )
        # duplicate import should be aborted
        result2 = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_IMPORT}, data=TESTDATA
        )
    assert result["type"] == data_entry_flow.RESULT_TYPE_CREATE_ENTRY
    assert result["title"] == "cam1"
    assert result2["type"] == data_entry_flow.RESULT_TYPE_ABORT


@respx.mock
async def test_import_invalid_still_image(hass, fakeimgbytes_png, fakevidcontainer):
    """Test configuration.yaml import used during migration."""
    respx.get("http://127.0.0.1/testurl/1").respond(stream=fakeimgbytes_png)
    with patch("av.open", return_value=fakevidcontainer):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_IMPORT}, data=TESTDATA
        )
    assert result["type"] == data_entry_flow.RESULT_TYPE_CREATE_ENTRY
    assert result["title"] == "cam1"


async def test_import_other_error(hass, fakevidcontainer):
    """Test that none-specific import errors are caught and logged."""
    with patch(
        "homeassistant.components.generic.camera.GenericCamera.async_camera_image",
        return_value=None,
    ), patch(
        "av.open",
        return_value=fakevidcontainer,
        side_effect=OSError("other error"),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_IMPORT}, data=TESTDATA
        )
    assert result["type"] == data_entry_flow.RESULT_TYPE_ABORT


# These above can be deleted after deprecation period is finished.


@respx.mock
async def test_unload_entry(hass, fakeimgbytes_png, fakevidcontainer):
    """Test unloading the generic IP Camera entry."""
    respx.get("http://127.0.0.1/testurl/1").respond(stream=fakeimgbytes_png)

    await setup.async_setup_component(hass, "persistent_notification", {})
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] == "form"
    assert result["errors"] == {}

    with patch("av.open", return_value=fakevidcontainer):
        mock_entry = MockConfigEntry(domain=DOMAIN, data=TESTDATA)
        mock_entry.add_to_hass(hass)
        assert await homeassistant.components.generic.async_setup_entry(
            hass, mock_entry
        )
        await hass.async_block_till_done()
        assert await homeassistant.components.generic.async_unload_entry(
            hass, mock_entry
        )
        await hass.async_block_till_done()
