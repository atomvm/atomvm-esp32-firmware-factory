#!/usr/bin/env escript
%% -*- erlang -*-
%%! -noshell
%%
%% Verify an AtomVM firmware bundle before it is uploaded:
%%   - it opens with zip:unzip/2 in memory (so it uses DEFLATE or STORE only),
%%   - it contains exactly the expected members,
%%   - <stem>.img.sha256 matches <stem>.img.
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
    Sha = Stem ++ ".img.sha256",
    Expected = [Img, Sha, "sdkconfig", "partitions.csv", "FLASH.txt"],
    Names = [Name || {Name, _} <- Members],
    case lists:sort(Names) =:= lists:sort(Expected) of
        false ->
            {error, {members, Names, Expected}};
        true ->
            {_, ImgBin} = lists:keyfind(Img, 1, Members),
            {_, ShaBin} = lists:keyfind(Sha, 1, Members),
            check_sha256(Img, ImgBin, ShaBin)
    end.

check_sha256(Img, ImgBin, ShaBin) ->
    Digest = binary:encode_hex(crypto:hash(sha256, ImgBin), lowercase),
    ImgName = list_to_binary(Img),
    case binary:split(ShaBin, [<<" ">>, <<"\n">>], [global, trim_all]) of
        [Digest, ImgName] ->
            io:format("~s: ~B bytes, sha256 ~s~n", [Img, byte_size(ImgBin), Digest]),
            ok;
        [Digest, OtherName] ->
            {error, {sha256_name, OtherName, ImgName}};
        [Hex, _Name] ->
            {error, {sha256_mismatch, Hex, Digest}};
        Other ->
            {error, {sha256_file, Other}}
    end.
