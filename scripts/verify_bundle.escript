#!/usr/bin/env escript
%% -*- erlang -*-
%%! -noshell
%%
%% Verify an AtomVM firmware bundle before it is uploaded:
%%   - it opens with zip:unzip/2 in memory (so it uses DEFLATE or STORE only),
%%   - it contains exactly the expected members,
%%   - <stem>.img.sha256 and SHA256SUMS list the expected names, and every
%%     listed digest matches its member.
%%
%% usage: verify_bundle.escript <bundle.zip> <stem>

main([Zip, Stem]) ->
    try verify(Zip, Stem) of
        ok ->
            io:format("ok: ~s~n", [Zip]),
            halt(0);
        {error, Reason} ->
            io:format(standard_error, "verify_bundle: ~s: ~p~n", [Zip, Reason]),
            halt(1)
    catch
        Class:Reason:Stack ->
            io:format(standard_error, "verify_bundle: ~s: ~p~n", [Zip, {Class, Reason, Stack}]),
            halt(1)
    end;
main(_) ->
    io:format(standard_error, "usage: verify_bundle.escript <bundle.zip> <stem>~n", []),
    halt(2).

verify(Zip, Stem) ->
    case zip:unzip(Zip, [memory]) of
        {ok, Members} -> check_members(Members, Stem);
        Other -> {error, {unzip, Other}}
    end.

check_members(Members, Stem) ->
    Img = Stem ++ ".img",
    Parts = ["bootloader.bin", "partition-table.bin", "atomvm-esp32.bin", boot_library(Stem),
             "atomvm-esp32.elf", "atomvm-esp32.map", "bootloader.elf", "bootloader.map", "prefix_map_gdbinit"],
    Summed = [Img, "sdkconfig", "partitions.csv", "FLASH.txt" | Parts],
    Expected = [Img, Img ++ ".sha256", "sdkconfig", "partitions.csv", "FLASH.txt"] ++ Parts ++ ["SHA256SUMS"],
    Names = [Name || {Name, _} <- Members],
    case lists:sort(Names) =:= lists:sort(Expected) of
        false ->
            {error, {members, Names, Expected}};
        true ->
            case check_sums(Img ++ ".sha256", [Img], Members) of
                ok -> check_sums("SHA256SUMS", Summed, Members);
                Error -> Error
            end
    end.

boot_library(Stem) ->
    case string:find(Stem, "-elixir-") of
        nomatch -> "esp32boot.avm";
        _ -> "elixir_esp32boot.avm"
    end.

check_sums(SumsName, Names, Members) ->
    {_, Sums} = lists:keyfind(SumsName, 1, Members),
    case parse_sums(binary:split(Sums, <<"\n">>, [global, trim_all]), []) of
        {ok, Listed} ->
            case [binary_to_list(Name) || {_, Name} <- Listed] of
                Names -> check_digests(Listed, Members);
                ListedNames -> {error, {sha256_names, SumsName, ListedNames, Names}}
            end;
        Error ->
            Error
    end.

parse_sums([], Acc) ->
    {ok, lists:reverse(Acc)};
parse_sums([<<Hex:64/binary, "  ", Name/binary>> | Rest], Acc) ->
    parse_sums(Rest, [{Hex, Name} | Acc]);
parse_sums([Line | _], _Acc) ->
    {error, {sha256_file, Line}}.

check_digests([], _Members) ->
    ok;
check_digests([{Hex, Name} | Rest], Members) ->
    Path = binary_to_list(Name),
    {_, Data} = lists:keyfind(Path, 1, Members),
    case binary:encode_hex(crypto:hash(sha256, Data), lowercase) of
        Hex ->
            io:format("~s: ~B bytes, sha256 ~s~n", [Path, byte_size(Data), Hex]),
            check_digests(Rest, Members);
        Digest ->
            {error, {sha256_mismatch, Path, Hex, Digest}}
    end.
